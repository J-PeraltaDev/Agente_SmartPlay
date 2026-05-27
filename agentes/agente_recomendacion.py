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

from datetime import datetime
from rdflib import Graph, Namespace, Literal, XSD
from rdflib.namespace import RDF
from .agente_perfil_usuario import AgentePerfilUsuario

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
        for r in self.graph.query(query):
            jid = str(r["juegoUri"]).split("#")[-1]
            if jid not in catalogo:
                catalogo[jid] = {
                    "id":         jid,
                    "uri":        str(r["juegoUri"]),
                    "titulo":     str(r["titulo"]),
                    "precio":     float(r["precio"]),
                    "anio":       int(r["anio"]),
                    "generos":    set(),
                    "plataformas": set(),
                    "mecanicas":  set(),
                    "similares":  set(),
                }
            catalogo[jid]["generos"].add(str(r["generoUri"]).split("#")[-1])
            catalogo[jid]["plataformas"].add(str(r["plataformaUri"]).split("#")[-1])

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
        query = f"""
        PREFIX sp: <http://www.smartplay.com/ontology#>
        SELECT ?juegoUri
        WHERE {{
            sp:{usuario_id} sp:tieneHistorial ?entrada .
            ?entrada sp:sobreJuego  ?juegoUri ;
                     sp:calificacion ?cal .
            FILTER(?cal >= {self.CALIFICACION_ALTA})
        }}
        """
        return {str(r["juegoUri"]).split("#")[-1]
                for r in self.graph.query(query)}

    def _obtener_usuarios_similares(self, usuario_id: str) -> list[str]:
        """
        Consulta SPARQL: encuentra usuarios que comparten al menos un género
        de interés con el usuario objetivo.

        Returns:
            Lista de usuario_ids similares (excluyendo al usuario objetivo).
        """
        query = f"""
        PREFIX sp: <http://www.smartplay.com/ontology#>
        SELECT DISTINCT ?otroUri
        WHERE {{
            sp:{usuario_id} sp:interesadoEn ?genero .
            ?otroUri a sp:Usuario ;
                     sp:interesadoEn ?genero .
            FILTER(?otroUri != sp:{usuario_id})
        }}
        """
        return [str(r["otroUri"]).split("#")[-1]
                for r in self.graph.query(query)]

    def _obtener_calificacion_de_usuario(self, usuario_id: str, juego_id: str) -> float | None:
        """
        Consulta SPARQL: devuelve la calificación que un usuario le dio a un juego,
        o None si no lo ha jugado.
        """
        query = f"""
        PREFIX sp: <http://www.smartplay.com/ontology#>
        SELECT ?cal
        WHERE {{
            sp:{usuario_id} sp:tieneHistorial ?entrada .
            ?entrada sp:sobreJuego  sp:{juego_id} ;
                     sp:calificacion ?cal .
        }}
        LIMIT 1
        """
        resultados = list(self.graph.query(query))
        return float(resultados[0]["cal"]) if resultados else None

    # ─────────────────────────────────────────────────────────────
    #  RAZONAMIENTO: cálculo de scores por estrategia
    # ─────────────────────────────────────────────────────────────

    def _score_contenido(self, perfil: dict, juego: dict,
                         bien_calificados: set[str], catalogo: dict) -> float:
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
            Score en [0.0, 1.0].
        """
        score = 0.0

        # 1. Coincidencia de géneros
        generos_usuario = set(perfil["generos"])
        generos_juego   = juego["generos"]
        if generos_usuario:
            coincidencia = len(generos_usuario & generos_juego) / len(generos_usuario)
            score += coincidencia * 0.40

        # 2. Compatibilidad de plataforma
        if perfil["plataforma"] in juego["plataformas"]:
            score += 0.30

        # 3. Similitud con juegos bien calificados del historial
        # Un juego gana puntos si algún juego que el usuario ama lo marca como similar
        for buen_juego_id in bien_calificados:
            buen_juego = catalogo.get(buen_juego_id)
            if buen_juego and juego["id"] in buen_juego.get("similares", set()):
                score += 0.30
                break  # Solo se contabiliza una vez

        return min(score, 1.0)

    def _score_colaborativo(self, usuario_id: str, juego_id: str) -> float:
        """
        Estrategia Colaborativa.
        Busca la calificación promedio del juego entre usuarios similares
        (aquellos que comparten géneros de interés). Pondera más fuerte a
        usuarios que comparten la plataforma preferida.

        Args:
            usuario_id: ID del usuario objetivo.
            juego_id:   ID del juego candidato.

        Returns:
            Score en [0.0, 1.0], o 0.0 si nadie similar jugó el juego.
        """
        perfil = self.agente_perfil.obtener_perfil(usuario_id)
        usuarios_similares = self._obtener_usuarios_similares(usuario_id)

        scores_ponderados = []
        for otro_id in usuarios_similares:
            cal = self._obtener_calificacion_de_usuario(otro_id, juego_id)
            if cal is None or cal < self.UMBRAL_COLABORATIVO:
                continue
            # Peso extra si comparten plataforma preferida
            perfil_otro = self.agente_perfil.obtener_perfil(otro_id)
            peso = 1.3 if (perfil_otro and
                           perfil_otro["plataforma"] == perfil["plataforma"]) else 1.0
            scores_ponderados.append((cal / 10.0) * peso)

        if not scores_ponderados:
            return 0.0
        return min(sum(scores_ponderados) / len(scores_ponderados), 1.0)

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
            print(f"[AgenteRecomendacion] ✗ Usuario '{usuario_id}' no encontrado.")
            return []

        jugados_uris  = self.agente_perfil.obtener_juegos_jugados_uris(usuario_id)
        catalogo      = self._obtener_catalogo()
        bien_calificados = self._obtener_juegos_bien_calificados(usuario_id)

        # Filtrar candidatos: excluir juegos ya jugados
        candidatos = [j for j in catalogo.values() if j["uri"] not in jugados_uris]
        if not candidatos:
            print(f"[AgenteRecomendacion] No hay juegos nuevos para '{usuario_id}'.")
            return []

        # ── Razonamiento: selección y aplicación de estrategia ────────────────
        if estrategia is None:
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
                score = self._score_contenido(perfil, juego, bien_calificados, catalogo)

            elif estrategia == "colaborativa":
                sc = self._score_colaborativo(usuario_id, juego["id"])
                # Fallback a contenido si no hay datos colaborativos
                score = sc if sc > 0 else (
                    self._score_contenido(perfil, juego, bien_calificados, catalogo) * 0.5
                )

            else:  # hibrida
                sc = self._score_contenido(perfil, juego, bien_calificados, catalogo)
                sl = self._score_colaborativo(usuario_id, juego["id"])
                score = (sc * 0.60 + sl * 0.40) if sl > 0 else sc

            if score > 0:
                candidatos_puntuados.append({
                    **juego,
                    "generos":    list(juego["generos"]),
                    "plataformas": list(juego["plataformas"]),
                    "mecanicas":  list(juego["mecanicas"]),
                    "similares":  list(juego["similares"]),
                    "score":       round(score, 4),
                    "estrategia":  estrategia_uri,
                    "estrategia_label": estrategia,
                })

        # Ordenar por score descendente y tomar top-N
        candidatos_puntuados.sort(key=lambda x: x["score"], reverse=True)
        top_n = candidatos_puntuados[:n]

        # ── Acción: persistir recomendaciones en el grafo ─────────────────────
        self._guardar_recomendaciones(usuario_id, top_n)
        print(f"[AgenteRecomendacion] ✓ {len(top_n)} recomendaciones generadas.")
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
        usuario_uri = SP[usuario_id]

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

            self.graph.add((rec_uri, RDF.type,                SP.Recomendacion))
            self.graph.add((rec_uri, SP.recomendadoPara,      usuario_uri))
            self.graph.add((rec_uri, SP.recomiendaJuego,      SP[rec["id"]]))
            self.graph.add((rec_uri, SP.usoEstrategia,        SP[rec["estrategia"]]))
            self.graph.add((rec_uri, SP.puntuacionRelevancia,
                            Literal(rec["score"], datatype=XSD.decimal)))
            self.graph.add((rec_uri, SP.fechaRecomendacion,
                            Literal(now, datatype=XSD.dateTime)))

        self.graph.serialize(self.agente_perfil.ruta_datos, format="turtle")

    def obtener_recomendaciones_guardadas(self, usuario_id: str) -> list[dict]:
        """
        Consulta SPARQL: recupera las recomendaciones ya almacenadas para
        un usuario, ordenadas por puntuación de relevancia descendente.

        Args:
            usuario_id: Nombre local del individuo usuario.

        Returns:
            Lista de dicts con título, score, estrategia y metadatos del juego.
        """
        query = f"""
        PREFIX sp: <http://www.smartplay.com/ontology#>
        SELECT ?titulo ?score ?estrategiaUri ?fecha ?precio ?anio ?generoUri
        WHERE {{
            ?rec a sp:Recomendacion ;
                 sp:recomendadoPara    sp:{usuario_id} ;
                 sp:recomiendaJuego    ?juegoUri ;
                 sp:puntuacionRelevancia ?score ;
                 sp:usoEstrategia      ?estrategiaUri .
            ?juegoUri sp:titulo           ?titulo ;
                      sp:precio           ?precio ;
                      sp:anioLanzamiento  ?anio ;
                      sp:perteneceAGenero ?generoUri .
            OPTIONAL {{ ?rec sp:fechaRecomendacion ?fecha }}
        }}
        ORDER BY DESC(?score)
        """
        vistas = set()
        recomendaciones = []
        for r in self.graph.query(query):
            titulo = str(r["titulo"])
            if titulo in vistas:
                continue
            vistas.add(titulo)
            recomendaciones.append({
                "titulo":    titulo,
                "score":     float(r["score"]),
                "estrategia": str(r["estrategiaUri"]).split("#")[-1],
                "fecha":     str(r["fecha"]) if r["fecha"] else "—",
                "precio":    float(r["precio"]),
                "anio":      int(r["anio"]),
                "genero":    str(r["generoUri"]).split("#")[-1],
            })
        return recomendaciones
