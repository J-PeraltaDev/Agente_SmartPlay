"""
=============================================================================
AGENTE DE PERFIL DE USUARIO — SmartPlay
=============================================================================
Descripción:
    Agente inteligente responsable de gestionar el perfil del usuario.
    Recolecta, actualiza e infiere preferencias basándose en las
    interacciones del usuario con el sistema (calificaciones, tiempo jugado).

Tecnologías Web Semántico usadas:
    - RDF (rdflib): lectura y escritura de tripletas
    - OWL: carga de la ontología SmartPlay
    - SPARQL: consultas sobre el grafo de conocimiento
=============================================================================
"""

import re
import uuid
from datetime import datetime
from pathlib import Path
from rdflib import Graph, Namespace, Literal, URIRef, XSD
from rdflib.namespace import RDF

# Namespace de la ontología SmartPlay
SP = Namespace("http://www.smartplay.com/ontology#")
LOCAL_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def id_local_valido(local_id: str) -> bool:
    """Valida nombres locales usados para recursos SmartPlay."""
    return bool(local_id and LOCAL_ID_RE.fullmatch(local_id))


def recurso_sp(local_id: str) -> URIRef:
    """Convierte un ID local validado en URIRef de la ontología SmartPlay."""
    if not id_local_valido(local_id):
        raise ValueError(f"ID local inválido: {local_id!r}")
    return SP[local_id]


class AgentePerfilUsuario:
    """
    Agente que recolecta y actualiza las preferencias del usuario
    basadas en sus interacciones con el sistema.

    Ciclo de vida del agente:
        1. Percibe: lee el grafo RDF para obtener el estado actual del usuario.
        2. Razona: infiere nuevos intereses a partir de calificaciones altas.
        3. Actúa: añade tripletas al grafo y lo persiste en disco.
    """

    def __init__(
        self,
        ruta_datos: str | list[str],
        ruta_ontologia: str = None,
        ruta_usuarios: str = None,
        ruta_interacciones: str = None,
    ):
        """
        Inicializa el agente cargando los datos RDF y la ontología OWL.

        Args:
            ruta_datos:     Ruta al archivo .ttl con los datos de SmartPlay.
            ruta_ontologia: Ruta al archivo .owl con la ontología (opcional).
        """
        self.rutas_datos = [ruta_datos] if isinstance(ruta_datos, str) else list(ruta_datos)
        self.ruta_datos = self.rutas_datos[0] if self.rutas_datos else None
        self.ruta_usuarios = ruta_usuarios
        self.ruta_interacciones = ruta_interacciones
        self.graph = Graph()
        self._bind_prefixes(self.graph)

        # Cargar la ontología en un grafo separado para no mezclar el esquema
        # OWL con las instancias que se persisten en el archivo Turtle.
        self.ontology_graph = Graph()
        self._bind_prefixes(self.ontology_graph)
        if ruta_ontologia:
            self.ontology_graph.parse(ruta_ontologia, format="xml")
            print(f"[AgentePerfilUsuario] Ontología cargada.")

        # Cargar datos de instancias desde uno o varios archivos Turtle.
        for ruta in self.rutas_datos:
            if ruta and Path(ruta).exists():
                self.graph.parse(ruta, format="turtle")
        print(f"[AgentePerfilUsuario] Grafo listo: {len(self.graph)} tripletas.")

    def _bind_prefixes(self, graph: Graph):
        graph.bind("sp", SP)
        graph.bind("xsd", XSD)

    def guardar(self):
        """Persiste los datos mutables sin mezclar catálogo ni ontología."""
        if self.ruta_usuarios and self.ruta_interacciones:
            self._guardar_usuarios()
            self._guardar_interacciones()
            return

        # Compatibilidad con la versión anterior.
        if self.ruta_datos:
            self.graph.serialize(self.ruta_datos, format="turtle")

    def _serializar_subgrafo(self, ruta: str, triples: list[tuple]):
        subgraph = Graph()
        self._bind_prefixes(subgraph)
        for triple in triples:
            subgraph.add(triple)
        subgraph.serialize(ruta, format="turtle")

    def _guardar_usuarios(self):
        triples = []
        usuarios = set(self.graph.subjects(RDF.type, SP.Usuario))
        for usuario_uri in usuarios:
            triples.extend(self.graph.triples((usuario_uri, None, None)))
        self._serializar_subgrafo(self.ruta_usuarios, triples)

    def _guardar_interacciones(self):
        triples = []
        tipos_mutables = (SP.EntradaHistorial, SP.Recomendacion)
        for tipo in tipos_mutables:
            for sujeto in self.graph.subjects(RDF.type, tipo):
                triples.extend(self.graph.triples((sujeto, None, None)))
        self._serializar_subgrafo(self.ruta_interacciones, triples)

    def obtener_recurso_usuario(self, usuario_id: str) -> URIRef | None:
        try:
            usuario_uri = recurso_sp(usuario_id)
        except ValueError:
            return None
        if (usuario_uri, RDF.type, SP.Usuario) not in self.graph:
            return None
        return usuario_uri

    def obtener_recurso_juego(self, juego_id: str) -> URIRef | None:
        try:
            juego_uri = recurso_sp(juego_id)
        except ValueError:
            return None
        if (juego_uri, RDF.type, SP.Videojuego) not in self.graph:
            return None
        return juego_uri


    # ─────────────────────────────────────────────────────────────
    #  PERCEPCIÓN: consultas SPARQL de lectura
    # ─────────────────────────────────────────────────────────────

    def obtener_todos_usuarios(self) -> list[dict]:
        """
        Consulta SPARQL: lista todos los usuarios del grafo.

        Returns:
            Lista de dicts con los datos principales de cada usuario.
        """
        query = """
        PREFIX sp: <http://www.smartplay.com/ontology#>
        SELECT DISTINCT ?usuarioUri ?nombre ?edad ?region ?plataformaUri ?generoUri ?entrada
        WHERE {
            ?usuarioUri a sp:Usuario ;
                        sp:nombreUsuario ?nombre .
            OPTIONAL { ?usuarioUri sp:edad ?edad . }
            OPTIONAL { ?usuarioUri sp:region ?region . }
            OPTIONAL { ?usuarioUri (sp:plataformaPreferida|sp:prefierePlataforma) ?plataformaUri . }
            OPTIONAL { ?usuarioUri sp:interesadoEn ?generoUri . }
            OPTIONAL { ?usuarioUri sp:tieneHistorial ?entrada . }
        }
        ORDER BY ?nombre
        """
        usuarios_por_id = {}
        for r in self.graph.query(query):
            usuario_id = str(r["usuarioUri"]).split("#")[-1]
            usuario = usuarios_por_id.setdefault(usuario_id, {
                "id": usuario_id,
                "nombre": str(r["nombre"]),
                "edad": None,
                "region": "Sin región",
                "plataforma": "Sin plataforma",
                "generos": set(),
                "historial_count": 0,
                "_historiales": set(),
            })

            if r.get("edad") is not None:
                usuario["edad"] = int(r["edad"])
            if r.get("region") is not None:
                usuario["region"] = str(r["region"])
            if r.get("plataformaUri") is not None:
                usuario["plataforma"] = str(r["plataformaUri"]).split("#")[-1]
            if r.get("generoUri") is not None:
                usuario["generos"].add(str(r["generoUri"]).split("#")[-1])
            if r.get("entrada") is not None:
                usuario["_historiales"].add(str(r["entrada"]))

        usuarios = []
        for usuario in usuarios_por_id.values():
            usuario["generos"] = sorted(usuario["generos"])
            usuario["historial_count"] = len(usuario["_historiales"])
            del usuario["_historiales"]
            usuarios.append(usuario)

        return sorted(usuarios, key=lambda u: u["nombre"])

    def obtener_perfil(self, usuario_id: str) -> dict | None:
        """
        Consulta SPARQL: recupera el perfil completo de un usuario.

        Args:
            usuario_id: Nombre local del individuo (ej. 'Juan', 'Camilo').

        Returns:
            Dict con nombre, edad, región, plataforma, géneros e historial,
            o None si el usuario no existe.
        """
        usuario_uri = self.obtener_recurso_usuario(usuario_id)
        if usuario_uri is None:
            return None

        query = """
        PREFIX sp: <http://www.smartplay.com/ontology#>
        SELECT ?nombre ?edad ?region ?plataformaUri
        WHERE {
            ?usuario a sp:Usuario ;
                     sp:nombreUsuario ?nombre .
            OPTIONAL { ?usuario sp:edad ?edad . }
            OPTIONAL { ?usuario sp:region ?region . }
            OPTIONAL { ?usuario (sp:plataformaPreferida|sp:prefierePlataforma) ?plataformaUri . }
        }
        LIMIT 1
        """
        resultados = list(self.graph.query(query, initBindings={"usuario": usuario_uri}))
        if not resultados:
            return None

        primera = resultados[0]
        generos = {
            str(genero_uri).split("#")[-1]
            for genero_uri in self.graph.objects(usuario_uri, SP.interesadoEn)
        }
        return {
            "id":        usuario_id,
            "nombre":    str(primera["nombre"]),
            "edad":      int(primera["edad"]) if primera.get("edad") is not None else None,
            "region":    str(primera["region"]) if primera.get("region") is not None else "Sin región",
            "plataforma": (
                str(primera["plataformaUri"]).split("#")[-1]
                if primera.get("plataformaUri") is not None
                else "Sin plataforma"
            ),
            "generos":   sorted(generos),
            "historial": self.obtener_historial(usuario_id),
        }

    def obtener_historial(self, usuario_id: str) -> list[dict]:
        """
        Consulta SPARQL: recupera el historial de juegos del usuario,
        ordenado por fecha descendente.

        Args:
            usuario_id: Nombre local del individuo usuario.

        Returns:
            Lista de dicts con título, calificación, tiempo jugado y género.
        """
        usuario_uri = self.obtener_recurso_usuario(usuario_id)
        if usuario_uri is None:
            return []

        query = """
        PREFIX sp: <http://www.smartplay.com/ontology#>
        SELECT ?titulo ?calificacion ?tiempoJugado ?fecha ?juegoUri ?generoUri
        WHERE {
            ?usuario sp:tieneHistorial ?entrada .
            ?entrada sp:sobreJuego   ?juegoUri ;
                     sp:calificacion  ?calificacion ;
                     sp:tiempoJugado  ?tiempoJugado ;
                     sp:fechaRegistro ?fecha .
            ?juegoUri sp:titulo           ?titulo ;
                      sp:perteneceAGenero ?generoUri .
        }
        ORDER BY DESC(?fecha)
        """
        historial = []
        vistos = set()
        for r in self.graph.query(query, initBindings={"usuario": usuario_uri}):
            juego_id = str(r["juegoUri"]).split("#")[-1]
            # Evitar duplicados si un juego tiene múltiples géneros
            if juego_id in vistos:
                continue
            vistos.add(juego_id)
            historial.append({
                "juego_id":     juego_id,
                "titulo":       str(r["titulo"]),
                "calificacion": float(r["calificacion"]),
                "tiempo_jugado": int(r["tiempoJugado"]),
                "fecha":        str(r["fecha"]),
                "genero":       str(r["generoUri"]).split("#")[-1],
            })
        return historial

    def obtener_juegos_jugados_uris(self, usuario_id: str) -> set[str]:
        """
        Consulta SPARQL: retorna el conjunto de URIs de juegos ya jugados.
        Usado por el AgenteRecomendacion para filtrar candidatos.

        Args:
            usuario_id: Nombre local del individuo usuario.

        Returns:
            Set de URIs (strings) de videojuegos en el historial.
        """
        usuario_uri = self.obtener_recurso_usuario(usuario_id)
        if usuario_uri is None:
            return set()

        query = """
        PREFIX sp: <http://www.smartplay.com/ontology#>
        SELECT ?juegoUri
        WHERE {
            ?usuario sp:tieneHistorial ?entrada .
            ?entrada sp:sobreJuego ?juegoUri .
        }
        """
        return {
            str(r["juegoUri"])
            for r in self.graph.query(query, initBindings={"usuario": usuario_uri})
        }

    # ─────────────────────────────────────────────────────────────
    #  ACCIÓN: escritura de nuevas tripletas al grafo
    # ─────────────────────────────────────────────────────────────

    def actualizar_preferencia(self, usuario_id: str, juego_id: str,
                                calificacion: float, tiempo_jugado: int) -> bool:
        """
        Acción principal del agente: registra una nueva interacción del usuario.

        Lógica de razonamiento:
            - Crea una EntradaHistorial nueva con los datos de la sesión.
            - Si calificación >= 8.0, infiere automáticamente el género del juego
              como nuevo interés del usuario (si no lo tiene ya declarado).

        Args:
            usuario_id:    Nombre local del individuo usuario.
            juego_id:      Nombre local del individuo videojuego.
            calificacion:  Nota del usuario (0.0 – 10.0).
            tiempo_jugado: Horas jugadas en esta sesión.

        Returns:
            True si la operación fue exitosa, False en caso contrario.
        """
        usuario_uri = self.obtener_recurso_usuario(usuario_id)
        juego_uri = self.obtener_recurso_juego(juego_id)

        # Verificar existencia de entidades
        if usuario_uri is None:
            print(f"[AgentePerfilUsuario] ERROR: Usuario '{usuario_id}' no existe en el grafo.")
            return False
        if juego_uri is None:
            print(f"[AgentePerfilUsuario] ERROR: Videojuego '{juego_id}' no existe en el grafo.")
            return False

        # ── PERCEPCIÓN: construir nueva EntradaHistorial ──────────────────────
        entrada_id  = f"H{usuario_id}_{juego_id}_{uuid.uuid4().hex[:6]}"
        entrada_uri = SP[entrada_id]

        self.graph.add((entrada_uri, RDF.type,          SP.EntradaHistorial))
        self.graph.add((entrada_uri, SP.sobreJuego,     juego_uri))
        self.graph.add((entrada_uri, SP.calificacion,
                        Literal(calificacion, datatype=XSD.decimal)))
        self.graph.add((entrada_uri, SP.tiempoJugado,
                        Literal(tiempo_jugado, datatype=XSD.integer)))
        self.graph.add((entrada_uri, SP.fechaRegistro,
                        Literal(datetime.now().isoformat(), datatype=XSD.dateTime)))
        self.graph.add((usuario_uri, SP.tieneHistorial, entrada_uri))

        # ── RAZONAMIENTO: inferir nuevos géneros de interés ──────────────────
        if calificacion >= 8.0:
            for genero_uri in self.graph.objects(juego_uri, SP.perteneceAGenero):
                if (usuario_uri, SP.interesadoEn, genero_uri) not in self.graph:
                    self.graph.add((usuario_uri, SP.interesadoEn, genero_uri))
                    genero_label = str(genero_uri).split("#")[-1]
                    print(f"[AgentePerfilUsuario] Nuevo interés inferido: {genero_label}")

        # ── ACCIÓN: persistir cambios ─────────────────────────────────────────
        self.guardar()
        print(f"[AgentePerfilUsuario] OK: Historial de '{usuario_id}' actualizado "
              f"-> {juego_id} ({calificacion}/10, {tiempo_jugado}h)")
        return True
