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

import uuid
from datetime import datetime
from rdflib import Graph, Namespace, Literal, XSD
from rdflib.namespace import RDF

# Namespace de la ontología SmartPlay
SP = Namespace("http://www.smartplay.com/ontology#")


class AgentePerfilUsuario:
    """
    Agente que recolecta y actualiza las preferencias del usuario
    basadas en sus interacciones con el sistema.

    Ciclo de vida del agente:
        1. Percibe: lee el grafo RDF para obtener el estado actual del usuario.
        2. Razona: infiere nuevos intereses a partir de calificaciones altas.
        3. Actúa: añade tripletas al grafo y lo persiste en disco.
    """

    def __init__(self, ruta_datos: str, ruta_ontologia: str = None):
        """
        Inicializa el agente cargando los datos RDF y la ontología OWL.

        Args:
            ruta_datos:     Ruta al archivo .ttl con los datos de SmartPlay.
            ruta_ontologia: Ruta al archivo .owl con la ontología (opcional).
        """
        self.ruta_datos = ruta_datos
        self.graph = Graph()

        # Cargar ontología primero (esquema)
        if ruta_ontologia:
            self.graph.parse(ruta_ontologia, format="xml")
            print(f"[AgentePerfilUsuario] Ontología cargada.")

        # Cargar datos de instancias
        self.graph.parse(ruta_datos, format="turtle")
        print(f"[AgentePerfilUsuario] Grafo listo: {len(self.graph)} tripletas.")

    # ─────────────────────────────────────────────────────────────
    #  PERCEPCIÓN: consultas SPARQL de lectura
    # ─────────────────────────────────────────────────────────────

    def obtener_todos_usuarios(self) -> list[dict]:
        """
        Consulta SPARQL: lista todos los usuarios del grafo.

        Returns:
            Lista de dicts con id, nombre y plataforma de cada usuario.
        """
        query = """
        PREFIX sp: <http://www.smartplay.com/ontology#>
        SELECT DISTINCT ?usuarioUri ?nombre ?plataformaUri
        WHERE {
            ?usuarioUri a sp:Usuario ;
                        sp:nombreUsuario ?nombre ;
                        sp:plataformaPreferida ?plataformaUri .
        }
        ORDER BY ?nombre
        """
        usuarios = []
        for r in self.graph.query(query):
            usuarios.append({
                "id":        str(r["usuarioUri"]).split("#")[-1],
                "nombre":    str(r["nombre"]),
                "plataforma": str(r["plataformaUri"]).split("#")[-1],
            })
        return usuarios

    def obtener_perfil(self, usuario_id: str) -> dict | None:
        """
        Consulta SPARQL: recupera el perfil completo de un usuario.

        Args:
            usuario_id: Nombre local del individuo (ej. 'Juan', 'Camilo').

        Returns:
            Dict con nombre, edad, región, plataforma, géneros e historial,
            o None si el usuario no existe.
        """
        query = f"""
        PREFIX sp: <http://www.smartplay.com/ontology#>
        SELECT ?nombre ?edad ?region ?plataformaUri ?generoUri
        WHERE {{
            sp:{usuario_id} a sp:Usuario ;
                sp:nombreUsuario ?nombre ;
                sp:edad          ?edad ;
                sp:region        ?region ;
                sp:plataformaPreferida ?plataformaUri ;
                sp:interesadoEn  ?generoUri .
        }}
        """
        resultados = list(self.graph.query(query))
        if not resultados:
            return None

        primera = resultados[0]
        return {
            "id":        usuario_id,
            "nombre":    str(primera["nombre"]),
            "edad":      int(primera["edad"]),
            "region":    str(primera["region"]),
            "plataforma": str(primera["plataformaUri"]).split("#")[-1],
            "generos":   list({str(r["generoUri"]).split("#")[-1] for r in resultados}),
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
        query = f"""
        PREFIX sp: <http://www.smartplay.com/ontology#>
        SELECT ?titulo ?calificacion ?tiempoJugado ?fecha ?juegoUri ?generoUri
        WHERE {{
            sp:{usuario_id} sp:tieneHistorial ?entrada .
            ?entrada sp:sobreJuego   ?juegoUri ;
                     sp:calificacion  ?calificacion ;
                     sp:tiempoJugado  ?tiempoJugado ;
                     sp:fechaRegistro ?fecha .
            ?juegoUri sp:titulo           ?titulo ;
                      sp:perteneceAGenero ?generoUri .
        }}
        ORDER BY DESC(?fecha)
        """
        historial = []
        vistos = set()
        for r in self.graph.query(query):
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
        query = f"""
        PREFIX sp: <http://www.smartplay.com/ontology#>
        SELECT ?juegoUri
        WHERE {{
            sp:{usuario_id} sp:tieneHistorial ?entrada .
            ?entrada sp:sobreJuego ?juegoUri .
        }}
        """
        return {str(r["juegoUri"]) for r in self.graph.query(query)}

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
        usuario_uri = SP[usuario_id]
        juego_uri   = SP[juego_id]

        # Verificar existencia de entidades
        if (usuario_uri, RDF.type, SP.Usuario) not in self.graph:
            print(f"[AgentePerfilUsuario] ✗ Usuario '{usuario_id}' no existe en el grafo.")
            return False
        if (juego_uri, RDF.type, SP.Videojuego) not in self.graph:
            print(f"[AgentePerfilUsuario] ✗ Videojuego '{juego_id}' no existe en el grafo.")
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
                    print(f"[AgentePerfilUsuario] ★ Nuevo interés inferido: {genero_label}")

        # ── ACCIÓN: persistir cambios ─────────────────────────────────────────
        self.graph.serialize(self.ruta_datos, format="turtle")
        print(f"[AgentePerfilUsuario] ✓ Historial de '{usuario_id}' actualizado "
              f"→ {juego_id} ({calificacion}/10, {tiempo_jugado}h)")
        return True
