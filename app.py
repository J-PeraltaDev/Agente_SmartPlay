"""
=============================================================================
SmartPlay — Interfaz Web (Flask)
=============================================================================
Integra los dos agentes inteligentes con una interfaz web simple donde
el usuario puede ver su perfil, calificar juegos y recibir recomendaciones.

Rutas:
    GET  /                          → Listado de usuarios
    GET  /perfil/<usuario_id>       → Perfil, historial y recomendaciones
    POST /calificar                 → Llama a AgentePerfilUsuario.actualizar_preferencia()
    POST /recomendar/<usuario_id>   → Llama a AgenteRecomendacion.recomendar()
    GET  /api/recomendar/<id>       → Endpoint JSON para integración externa
=============================================================================
"""

import os
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from agentes.agente_perfil_usuario import AgentePerfilUsuario
from agentes.agente_recomendacion import AgenteRecomendacion

app = Flask(__name__)
app.secret_key = "smartplay_clave_secreta_2026"

# ── Rutas de archivos de datos ─────────────────────────────────────────────
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
RUTA_DATOS     = os.path.join(BASE_DIR, "datos", "SmartPlay_datos.ttl")
RUTA_ONTOLOGIA = os.path.join(BASE_DIR, "datos", "SmartPlay_Ontologia.owl")

# ── Inicialización de agentes (una sola vez al arrancar Flask) ─────────────
print("\n" + "="*60)
print("   SmartPlay — Inicializando agentes inteligentes...")
print("="*60)
agente_perfil = AgentePerfilUsuario(RUTA_DATOS, RUTA_ONTOLOGIA)
agente_rec    = AgenteRecomendacion(agente_perfil)
print("="*60 + "\n")


# ─────────────────────────────────────────────────────────────────────────────
#  RUTAS
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Página principal: lista de usuarios registrados en el grafo."""
    usuarios = agente_perfil.obtener_todos_usuarios()
    return render_template("index.html", usuarios=usuarios)


@app.route("/perfil/<usuario_id>")
def perfil(usuario_id):
    """
    Perfil completo de un usuario:
        - Información personal
        - Historial de juegos
        - Recomendaciones almacenadas
        - Formulario para calificar juegos nuevos
    """
    datos_perfil = agente_perfil.obtener_perfil(usuario_id)
    if not datos_perfil:
        flash(f"Usuario '{usuario_id}' no encontrado en el grafo.", "danger")
        return redirect(url_for("index"))

    # Juegos disponibles para calificar (no jugados aún)
    catalogo        = agente_rec._obtener_catalogo()
    jugados_uris    = agente_perfil.obtener_juegos_jugados_uris(usuario_id)
    juegos_nuevos   = sorted(
        [j for j in catalogo.values() if j["uri"] not in jugados_uris],
        key=lambda x: x["titulo"]
    )

    # Recomendaciones guardadas para este usuario
    recs = agente_rec.obtener_recomendaciones_guardadas(usuario_id)

    return render_template(
        "perfil.html",
        perfil=datos_perfil,
        juegos_nuevos=juegos_nuevos,
        recomendaciones=recs,
    )


@app.route("/calificar", methods=["POST"])
def calificar():
    """
    Recibe la calificación del usuario para un juego y llama al
    AgentePerfilUsuario para actualizar el historial e inferir intereses.
    """
    usuario_id    = request.form.get("usuario_id", "").strip()
    juego_id      = request.form.get("juego_id", "").strip()
    calificacion  = float(request.form.get("calificacion", 5.0))
    tiempo_jugado = int(request.form.get("tiempo_jugado", 1))

    if not usuario_id or not juego_id:
        flash("Datos incompletos en el formulario.", "warning")
        return redirect(url_for("index"))

    exito = agente_perfil.actualizar_preferencia(
        usuario_id, juego_id, calificacion, tiempo_jugado
    )

    if exito:
        flash(f"✓ Calificación {calificacion}/10 registrada exitosamente.", "success")
    else:
        flash("✗ Error al registrar la calificación. Verifica los datos.", "danger")

    return redirect(url_for("perfil", usuario_id=usuario_id))


@app.route("/recomendar/<usuario_id>", methods=["POST"])
def recomendar(usuario_id):
    """
    Ejecuta el AgenteRecomendacion con la estrategia seleccionada
    y redirige al perfil con las nuevas recomendaciones.
    """
    estrategia = request.form.get("estrategia") or None
    n          = int(request.form.get("n", 5))

    recs = agente_rec.recomendar(usuario_id, n=n, estrategia=estrategia)

    if recs:
        flash(f"✓ Se generaron {len(recs)} recomendaciones con estrategia "
              f"'{estrategia or 'automática'}'.", "success")
    else:
        flash("✗ No se pudieron generar recomendaciones para este usuario.", "warning")

    return redirect(url_for("perfil", usuario_id=usuario_id))


@app.route("/api/recomendar/<usuario_id>")
def api_recomendar(usuario_id):
    """
    Endpoint JSON: útil para pruebas desde la terminal o integración externa.

    Query params:
        estrategia: contenido | colaborativa | hibrida (default: automático)
        n:          cantidad de recomendaciones (default: 5)

    Ejemplo: GET /api/recomendar/Juan?estrategia=hibrida&n=3
    """
    estrategia = request.args.get("estrategia") or None
    n          = int(request.args.get("n", 5))
    recs       = agente_rec.recomendar(usuario_id, n=n, estrategia=estrategia)

    # Serializar sets a listas para JSON
    for r in recs:
        for campo in ("generos", "plataformas", "mecanicas", "similares"):
            if isinstance(r.get(campo), set):
                r[campo] = list(r[campo])

    return jsonify({
        "usuario":          usuario_id,
        "estrategia":       estrategia or "automática",
        "total":            len(recs),
        "recomendaciones":  recs,
    })


@app.route("/api/perfil/<usuario_id>")
def api_perfil(usuario_id):
    """Endpoint JSON: retorna el perfil completo del usuario."""
    datos = agente_perfil.obtener_perfil(usuario_id)
    if not datos:
        return jsonify({"error": f"Usuario '{usuario_id}' no encontrado"}), 404
    return jsonify(datos)


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=True, port=5000)
