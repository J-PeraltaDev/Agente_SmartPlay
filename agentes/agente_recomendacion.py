"""
=============================================================================
AGENTE DE RECOMENDACIÓN — SmartPlay
=============================================================================
Descripción:
    Agente inteligente que consulta el almacenamiento de datos RDF usando
    SPARQL para generar recomendaciones de videojuegos personalizadas.
    Implementa tres estrategias de recomendación:

    1. BasadaEnContenido: compara características del juego con el perfil
       del usuario (géneros, plataforma, juegos similares bien calificados).

    2. Colaborativa: encuentra usuarios con gustos similares y recomienda
       lo que ellos calificaron alto pero el usuario aún no jugó.

    3. Híbrida: combina ambas estrategias ponderando los scores (60/40).

Tecnologías Web Semántico usadas:
    - SPARQL: todas las consultas de recuperación de datos.
    - RDF (rdflib): escritura de nuevas Recomendacion al grafo.
    - OWL: individuos de EstrategiaRecomendacion (BasadaEnContenido, etc.)
=============================================================================
"""

from collections import Counter
from datetime import datetime
from math import log1p
from rdflib import Namespace, Literal, XSD
from rdflib.namespace import RDF
from .agente_perfil_usuario import AgentePerfilUsuario, recurso_sp

SP = Namespace("http://www.smartplay.com/ontology#")


class AgenteRecomendacion:
    """
    Agente que usa los perfiles de usuario y los datos de contenido
    para generar recomendaciones personalizadas de videojuegos.

    Ciclo de vida del agente:
        1. Percibe: recupera perfil del usuario y catálogo de juegos via SPARQL.
        2. Razona: puntúa juegos candidatos según la estrategia elegida.
        3. Actúa: persiste las recomendaciones generadas como tripletas RDF.
    """

    # Umbrales de scoring
    CALIFICACION_ALTA = 8.0   # Mínima para considerar un juego "bien calificado"
    UMBRAL_COLABORATIVO = 7.5 # Mínima calificación de otro usuario para contar

    def __init__(self, agente_perfil: AgentePerfilUsuario):
        """
        Inicializa el agente de recomendación compartiendo el grafo del
        agente de perfil (no carga datos dos veces).

        Args:
            agente_perfil: Instancia activa del AgentePerfilUsuario.
        """
        self.agente_perfil = agente_perfil
        self.graph = agente_perfil.graph
        print("[AgenteRecomendacion] Agente inicializado y conectado al grafo RDF.")

    # ─────────────────────────────────────────────────────────────
    #  PERCEPCIÓN: consultas SPARQL sobre el catálogo
    # ─────────────────────────────────────────────────────────────

    def _obtener_catalogo(self) -> dict[str, dict]:
        """
        Consulta SPARQL: recupera todos los videojuegos con sus atributos.

        Returns:
            Dict indexado por juego_id con géneros, plataformas, mecánicas
            y similares como sets Python.
        """
        query = """
        PREFIX sp: <http://www.smartplay.com/ontology#>
        SELECT DISTINCT ?juegoUri ?titulo ?precio ?anio ?generoUri ?plataformaUri
        WHERE {
            ?juegoUri a sp:Videojuego ;
                      sp:titulo           ?titulo ;
                      sp:precio           ?precio ;
                      sp:anioLanzamiento  ?anio ;
                      sp:perteneceAGenero ?generoUri ;
                      sp:disponibleEn     ?plataformaUri .
        }
        """
        catalogo = {}
        for fila in self.graph.query(query):
            if not isinstance(fila, (tuple, list)) or len(fila) != 6:
                continue
            juego_uri, titulo, precio, anio, genero_uri, plataforma_uri = fila
            jid = str(juego_uri).split("#")[-1]
            if jid not in catalogo:
                catalogo[jid] = {
                    "id":         jid,
                    "uri":        str(juego_uri),
                    "titulo":     str(titulo),
                    "precio":     float(precio),
                    "anio":       int(anio),
                    "generos":    set(),
                    "plataformas": set(),
                    "mecanicas":  set(),
                    "similares":  set(),
                }
            catalogo[jid]["generos"].add(str(genero_uri).split("#")[-1])
            catalogo[jid]["plataformas"].add(str(plataforma_uri).split("#")[-1])

        # Añadir mecánicas y similares por separado
        for jid, datos in catalogo.items():
            juego_uri = SP[jid]
            for mec in self.graph.objects(juego_uri, SP.usaMecanica):
                datos["mecanicas"].add(str(mec).split("#")[-1])
            for sim in self.graph.objects(juego_uri, SP.similarA):
                datos["similares"].add(str(sim).split("#")[-1])

        return catalogo

    def _obtener_juegos_bien_calificados(self, usuario_id: str) -> set[str]:
        """
        Consulta SPARQL: retorna IDs de juegos que el usuario calificó >= 8.0.
        Usado para detectar similitudes en filtrado por contenido.
        """
        usuario_uri = self.agente_perfil.obtener_recurso_usuario(usuario_id)
        if usuario_uri is None:
            return set()

        query = """
        PREFIX sp: <http://www.smartplay.com/ontology#>
        SELECT ?juegoUri
        WHERE {
            ?usuario sp:tieneHistorial ?entrada .
            ?entrada sp:sobreJuego  ?juegoUri ;
                     sp:calificacion ?cal .
            FILTER(?cal >= ?minCal)
        }
        """
        juegos = set()
        for fila in self.graph.query(
            query,
            initBindings={
                "usuario": usuario_uri,
                "minCal": Literal(self.CALIFICACION_ALTA, datatype=XSD.decimal),
            },
        ):
            if isinstance(fila, tuple) and len(fila) == 1:
                juegos.add(str(fila[0]).split("#")[-1])
        return juegos

    def _obtener_usuarios_similares(self, usuario_id: str) -> list[str]:
        """
        Consulta SPARQL: encuentra usuarios que comparten al menos un género
        de interés con el usuario objetivo.

        Returns:
            Lista de usuario_ids similares (excluyendo al usuario objetivo).
        """
        usuario_uri = self.agente_perfil.obtener_recurso_usuario(usuario_id)
        if usuario_uri is None:
            return []

        query = """
        PREFIX sp: <http://www.smartplay.com/ontology#>
        SELECT DISTINCT ?otroUri
        WHERE {
            ?usuario sp:interesadoEn ?genero .
            ?otroUri a sp:Usuario ;
                     sp:interesadoEn ?genero .
            FILTER(?otroUri != ?usuario)
        }
        """
        usuarios = []
        for fila in self.graph.query(query, initBindings={"usuario": usuario_uri}):
            if isinstance(fila, tuple) and len(fila) == 1:
                usuarios.append(str(fila[0]).split("#")[-1])
        return usuarios

    def _obtener_calificacion_de_usuario(self, usuario_id: str, juego_id: str) -> float | None:
        """
        Consulta SPARQL: devuelve la calificación que un usuario le dio a un juego,
        o None si no lo ha jugado.
        """
        usuario_uri = self.agente_perfil.obtener_recurso_usuario(usuario_id)
        if usuario_uri is None:
            return None
        try:
            juego_uri = recurso_sp(juego_id)
        except ValueError:
            return None

        query = """
        PREFIX sp: <http://www.smartplay.com/ontology#>
        SELECT ?cal
        WHERE {
            ?usuario sp:tieneHistorial ?entrada .
            ?entrada sp:sobreJuego  ?juego ;
                     sp:calificacion ?cal .
        }
        LIMIT 1
        """
        resultados = list(self.graph.query(
            query,
            initBindings={"usuario": usuario_uri, "juego": juego_uri},
        ))
        return float(str(resultados[0][0])) if resultados else None

    def _obtener_senales_usuario(self, perfil: dict, catalogo: dict) -> dict:
        """Resume señales de comportamiento para explicar y ajustar el scoring."""
        generos = Counter(perfil.get("generos") or [])
        mecanicas = Counter()
        juegos_favoritos = set()
        juegos_rechazados = set()
        precios_favoritos = []

        for entrada in perfil.get("historial", []):
            juego = catalogo.get(entrada.get("juego_id"))
            if not juego:
                continue
            calificacion = float(entrada.get("calificacion") or 0)
            horas = int(entrada.get("tiempo_jugado") or 0)
            peso = max(calificacion / 10.0, 0.1) * max(log1p(horas), 1.0)

            if calificacion >= 7.5:
                for genero in juego.get("generos", []):
                    generos[genero] += peso
                for mecanica in juego.get("mecanicas", []):
                    mecanicas[mecanica] += peso

            if calificacion >= self.CALIFICACION_ALTA:
                juegos_favoritos.add(juego["id"])
                precios_favoritos.append(juego.get("precio", 0))
            elif calificacion <= 5.0:
                juegos_rechazados.add(juego["id"])

        precio_promedio = (
            sum(precios_favoritos) / len(precios_favoritos)
            if precios_favoritos else None
        )
        return {
            "generos": generos,
            "mecanicas": mecanicas,
            "juegos_favoritos": juegos_favoritos,
            "juegos_rechazados": juegos_rechazados,
            "precio_promedio": precio_promedio,
        }

    # ─────────────────────────────────────────────────────────────
    #  RAZONAMIENTO: cálculo de scores por estrategia
    # ─────────────────────────────────────────────────────────────

    def _score_contenido(self, perfil: dict, juego: dict,
                         bien_calificados: set[str], catalogo: dict,
                         senales: dict | None = None) -> tuple[float, list[str]]:
        """
        Estrategia BasadaEnContenido.
        Pondera tres dimensiones:
            - Coincidencia de géneros (40%)
            - Compatibilidad de plataforma (30%)
            - Similitud con juegos bien calificados (30%)

        Args:
            perfil:          Perfil del usuario (de AgentePerfilUsuario).
            juego:           Datos del juego candidato.
            bien_calificados: IDs de juegos que el usuario calificó >= 8.0.
            catalogo:        Catálogo completo (para buscar similares).

        Returns:
            Tupla (score en [0.0, 1.0], explicaciones).
        """
        if not isinstance(perfil, dict) or not isinstance(juego, dict):
            return 0.0, []

        score = 0.0
        explicaciones = []
        senales = senales or {}

        # 1. Coincidencia de géneros declarados y aprendidos por historial
        generos_usuario = set(perfil.get("generos") or [])
        generos_juego   = set(juego.get("generos") or [])
        if generos_usuario:
            coincidencia = len(generos_usuario & generos_juego) / len(generos_usuario)
            if coincidencia:
                score += coincidencia * 0.30
                comunes = ", ".join(sorted(generos_usuario & generos_juego)[:3])
                explicaciones.append(f"Coincide con tus géneros de interés: {comunes}.")

        # 2. Compatibilidad de plataforma
        plataforma_usuario = perfil.get("plataforma")
        plataformas_juego = juego.get("plataformas") or []
        if plataforma_usuario and plataforma_usuario in plataformas_juego:
            score += 0.20
            explicaciones.append(f"Está disponible en tu plataforma preferida: {plataforma_usuario}.")

        # 3. Mecánicas aprendidas desde juegos con buenas calificaciones y muchas horas
        mecanicas_usuario = senales.get("mecanicas") or Counter()
        if mecanicas_usuario and juego.get("mecanicas"):
            mecanicas_juego = set(juego["mecanicas"])
            coincidencias = mecanicas_juego & set(mecanicas_usuario.keys())
            if coincidencias:
                total = sum(mecanicas_usuario.values()) or 1
                peso = min(sum(mecanicas_usuario[m] for m in coincidencias) / total, 1.0)
                score += peso * 0.15
                explicaciones.append(
                    "Comparte mecánicas que sueles valorar: "
                    + ", ".join(sorted(coincidencias)[:3])
                    + "."
                )

        # 4. Similitud explícita con juegos bien calificados del historial
        for buen_juego_id in bien_calificados:
            buen_juego = catalogo.get(buen_juego_id)
            if buen_juego and juego.get("id") in buen_juego.get("similares", set()):
                score += 0.20
                explicaciones.append(
                    f"Es similar a {buen_juego.get('titulo', buen_juego_id)}, que calificaste alto."
                )
                break  # Solo se contabiliza una vez

        # 5. Afinidad por precio y actualidad, como señales suaves de desempate
        precio_promedio = senales.get("precio_promedio")
        if precio_promedio is not None and juego.get("precio", 0) <= precio_promedio + 15:
            score += 0.05
            explicaciones.append("Su precio está cerca del rango de juegos que te han gustado.")

        if int(juego.get("anio") or 0) >= 2020:
            score += 0.05
            explicaciones.append("Es un lanzamiento relativamente reciente dentro del catálogo.")

        return min(score, 1.0), explicaciones[:5]

    def _score_colaborativo(self, usuario_id: str, juego_id: str) -> tuple[float, list[str]]:
        """
        Estrategia Colaborativa.
        Busca la calificación promedio del juego entre usuarios similares
        (aquellos que comparten géneros de interés). Pondera más fuerte a
        usuarios que comparten la plataforma preferida.

        Args:
            usuario_id: ID del usuario objetivo.
            juego_id:   ID del juego candidato.

        Returns:
            Tupla (score en [0.0, 1.0], explicaciones).
        """
        perfil = self.agente_perfil.obtener_perfil(usuario_id)
        if not perfil:
            return 0.0, []
        usuarios_similares = self._obtener_usuarios_similares(usuario_id)

        scores_ponderados = []
        usuarios_utiles = []
        for otro_id in usuarios_similares:
            cal = self._obtener_calificacion_de_usuario(otro_id, juego_id)
            if cal is None or cal < self.UMBRAL_COLABORATIVO:
                continue
            # Peso extra si comparten plataforma preferida
            perfil_otro = self.agente_perfil.obtener_perfil(otro_id)
            peso = 1.3 if (isinstance(perfil_otro, dict) and
                           perfil_otro.get("plataforma") == perfil.get("plataforma")) else 1.0
            scores_ponderados.append((cal / 10.0) * peso)
            usuarios_utiles.append((otro_id, cal))

        if not scores_ponderados:
            return 0.0, []
        promedio = min(sum(scores_ponderados) / len(scores_ponderados), 1.0)
        mejores = sorted(usuarios_utiles, key=lambda x: x[1], reverse=True)[:3]
        detalle = ", ".join(f"{uid} ({cal:.1f}/10)" for uid, cal in mejores)
        return promedio, [f"Usuarios con gustos similares lo calificaron alto: {detalle}."]

    def _diversificar_top_n(self, candidatos: list[dict], n: int) -> list[dict]:
        """Selecciona recomendaciones fuertes evitando un top dominado por un solo género."""
        seleccionados = []
        restantes = list(candidatos)
        conteo_generos = Counter()

        while restantes and len(seleccionados) < n:
            mejor = None
            mejor_valor = -1.0
            for candidato in restantes:
                generos = set(candidato.get("generos") or [])
                penalizacion = sum(max(conteo_generos[g] - 1, 0) for g in generos) * 0.04
                valor = candidato["score"] - penalizacion
                if valor > mejor_valor:
                    mejor = candidato
                    mejor_valor = valor

            restantes.remove(mejor)
            for genero in mejor.get("generos", []):
                conteo_generos[genero] += 1
            if mejor_valor < mejor["score"]:
                mejor = {
                    **mejor,
                    "explicaciones": mejor.get("explicaciones", []) + [
                        "Se mantuvo en el top cuidando variedad frente a otros géneros."
                    ],
                }
            seleccionados.append(mejor)

        return seleccionados

    # ─────────────────────────────────────────────────────────────
    #  ACCIÓN PRINCIPAL: generar y persistir recomendaciones
    # ─────────────────────────────────────────────────────────────

    def recomendar(self, usuario_id: str, n: int = 5,
                   estrategia: str = None) -> list[dict]:
        """
        Genera las top-N recomendaciones para un usuario.

        Selección automática de estrategia:
            - Sin historial → 'contenido' (no hay datos de comportamiento)
            - Con historial → 'hibrida' (aprovecha ambas fuentes de señal)

        Args:
            usuario_id: Nombre local del individuo usuario.
            n:          Número de recomendaciones a generar (default 5).
            estrategia: 'contenido' | 'colaborativa' | 'hibrida' | None (auto).

        Returns:
            Lista ordenada de dicts con información del juego y su score.
        """
        # ── Percepción ────────────────────────────────────────────────────────
        perfil = self.agente_perfil.obtener_perfil(usuario_id)
        if not perfil:
            print(f"[AgenteRecomendacion] ERROR: Usuario '{usuario_id}' no encontrado.")
            return []

        jugados_uris  = self.agente_perfil.obtener_juegos_jugados_uris(usuario_id)
        catalogo      = self._obtener_catalogo()
        bien_calificados = self._obtener_juegos_bien_calificados(usuario_id)
        senales = self._obtener_senales_usuario(perfil, catalogo)

        # Filtrar candidatos: excluir juegos ya jugados
        candidatos = [j for j in catalogo.values() if j["uri"] not in jugados_uris]
        if not candidatos:
            print(f"[AgenteRecomendacion] No hay juegos nuevos para '{usuario_id}'.")
            return []

        # ── Razonamiento: selección y aplicación de estrategia ────────────────
        if not estrategia:
            estrategia = "hibrida" if perfil["historial"] else "contenido"

        mapa_estrategia = {
            "contenido":    "BasadaEnContenido",
            "colaborativa": "Colaborativa",
            "hibrida":      "Hibrida",
        }
        estrategia_uri = mapa_estrategia.get(estrategia, "Hibrida")
        print(f"[AgenteRecomendacion] Estrategia: {estrategia} | Usuario: {usuario_id}")

        candidatos_puntuados = []
        for juego in candidatos:
            if estrategia == "contenido":
                score, explicaciones = self._score_contenido(
                    perfil, juego, bien_calificados, catalogo, senales
                )
                score_contenido = score
                score_colaborativo = 0.0

            elif estrategia == "colaborativa":
                sc, razones_colab = self._score_colaborativo(usuario_id, juego["id"])
                # Fallback a contenido si no hay datos colaborativos
                if sc > 0:
                    score = sc
                    explicaciones = razones_colab
                else:
                    score_base, razones_contenido = self._score_contenido(
                        perfil, juego, bien_calificados, catalogo, senales
                    )
                    score = score_base * 0.5
                    explicaciones = razones_contenido + [
                        "No hubo suficiente señal colaborativa; se usó contenido como respaldo."
                    ]
                    score_contenido = score_base
                if sc > 0:
                    score_contenido = 0.0
                score_colaborativo = sc

            else:  # hibrida
                sc, razones_contenido = self._score_contenido(
                    perfil, juego, bien_calificados, catalogo, senales
                )
                sl, razones_colab = self._score_colaborativo(usuario_id, juego["id"])
                score = (sc * 0.65 + sl * 0.35) if sl > 0 else sc
                explicaciones = razones_contenido + razones_colab
                score_contenido = sc
                score_colaborativo = sl

            if score > 0:
                candidatos_puntuados.append({
                    **juego,
                    "generos":    list(juego["generos"]),
                    "plataformas": list(juego["plataformas"]),
                    "mecanicas":  list(juego["mecanicas"]),
                    "similares":  list(juego["similares"]),
                    "score":       round(score, 4),
                    "score_contenido": round(score_contenido, 4),
                    "score_colaborativo": round(score_colaborativo, 4),
                    "estrategia":  estrategia_uri,
                    "estrategia_label": estrategia,
                    "explicaciones": explicaciones[:5],
                })

        # Ordenar por score descendente y tomar top-N
        candidatos_puntuados.sort(key=lambda x: x["score"], reverse=True)
        top_n = self._diversificar_top_n(candidatos_puntuados, n)

        # ── Acción: persistir recomendaciones en el grafo ─────────────────────
        self._guardar_recomendaciones(usuario_id, top_n)
        print(f"[AgenteRecomendacion] OK: {len(top_n)} recomendaciones generadas.")
        return top_n

    def _guardar_recomendaciones(self, usuario_id: str, recomendaciones: list[dict]):
        """
        Persiste las recomendaciones generadas como tripletas RDF en el grafo,
        usando los individuos de EstrategiaRecomendacion definidos en la ontología.
        Elimina las recomendaciones anteriores del usuario antes de insertar.

        Args:
            usuario_id:        ID del usuario.
            recomendaciones:   Lista ordenada de dicts con score y estrategia.
        """
        usuario_uri = self.agente_perfil.obtener_recurso_usuario(usuario_id)
        if usuario_uri is None:
            return

        # Eliminar recomendaciones previas del usuario
        recs_anteriores = list(self.graph.subjects(SP.recomendadoPara, usuario_uri))
        for rec_uri in recs_anteriores:
            for p, o in list(self.graph.predicate_objects(rec_uri)):
                self.graph.remove((rec_uri, p, o))

        # Insertar nuevas recomendaciones
        now = datetime.now().isoformat()
        for i, rec in enumerate(recomendaciones, start=1):
            rec_id  = f"Rec_{usuario_id}_{i:03d}"
            rec_uri = SP[rec_id]
            juego_uri = self.agente_perfil.obtener_recurso_juego(rec["id"])
            if juego_uri is None:
                continue

            self.graph.add((rec_uri, RDF.type,                SP.Recomendacion))
            self.graph.add((rec_uri, SP.recomendadoPara,      usuario_uri))
            self.graph.add((rec_uri, SP.recomiendaJuego,      juego_uri))
            self.graph.add((rec_uri, SP.usoEstrategia,        recurso_sp(rec["estrategia"])))
            self.graph.add((rec_uri, SP.puntuacionRelevancia,
                            Literal(rec["score"], datatype=XSD.decimal)))
            self.graph.add((rec_uri, SP.fechaRecomendacion,
                            Literal(now, datatype=XSD.dateTime)))
            for explicacion in rec.get("explicaciones", []):
                self.graph.add((rec_uri, SP.explicacion, Literal(explicacion)))

        self.agente_perfil.guardar()

    def obtener_recomendaciones_guardadas(self, usuario_id: str) -> list[dict]:
        """
        Recupera las recomendaciones ya almacenadas para
        un usuario, ordenadas por puntuación de relevancia descendente.

        Args:
            usuario_id: Nombre local del individuo usuario.

        Returns:
            Lista de dicts con título, score, estrategia y metadatos del juego.
        """
        usuario_uri = self.agente_perfil.obtener_recurso_usuario(usuario_id)
        if usuario_uri is None:
            return []

        recomendaciones = []
        for rec_uri in self.graph.subjects(SP.recomendadoPara, usuario_uri):
            if (rec_uri, RDF.type, SP.Recomendacion) not in self.graph:
                continue
            juego_uri = self.graph.value(rec_uri, SP.recomiendaJuego)
            score = self.graph.value(rec_uri, SP.puntuacionRelevancia)
            estrategia_uri = self.graph.value(rec_uri, SP.usoEstrategia)
            if not juego_uri or score is None or not estrategia_uri:
                continue

            titulo = self.graph.value(juego_uri, SP.titulo)
            precio = self.graph.value(juego_uri, SP.precio)
            anio = self.graph.value(juego_uri, SP.anioLanzamiento)
            genero_uri = next(self.graph.objects(juego_uri, SP.perteneceAGenero), None)
            if not titulo:
                continue

            explicaciones = [str(e) for e in self.graph.objects(rec_uri, SP.explicacion)]
            fecha = self.graph.value(rec_uri, SP.fechaRecomendacion)
            recomendaciones.append({
                "titulo":    str(titulo),
                "score":     float(str(score)),
                "estrategia": str(estrategia_uri).split("#")[-1],
                "fecha":     str(fecha) if fecha else "—",
                "precio":    float(str(precio)) if precio is not None else 0.0,
                "anio":      int(str(anio)) if anio is not None else 0,
                "genero":    str(genero_uri).split("#")[-1] if genero_uri else "SinGenero",
                "explicaciones": explicaciones,
            })
        return sorted(recomendaciones, key=lambda r: r["score"], reverse=True)
