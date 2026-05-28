"""
=============================================================================
SmartPlay — Interfaz Web (Flask)
=============================================================================
Integra los dos agentes inteligentes con una interfaz web simple donde
el usuario puede ver su perfil, calificar juegos y recibir recomendaciones.

Rutas:
    GET  /                          → Listado de usuarios
    GET  /login                     → Formulario de login
    POST /login                     → Autenticación
    GET  /logout                    → Cierra sesión
    GET  /register                  → Formulario de registro
    POST /register                  → Crea usuario nuevo
    GET  /perfil/<usuario_id>       → Perfil + recomendaciones (lectura siempre, edición solo si es el propio)
    POST /calificar                 → Solo para el usuario logueado
    POST /recomendar/<usuario_id>   → Llama a AgenteRecomendacion.recomendar()
    GET  /api/recomendar/<id>       → Endpoint JSON
=============================================================================
"""

import os
import re
from functools import wraps
from flask import (Flask, render_template, request, redirect,
                   url_for, flash, jsonify, session)
from werkzeug.security import generate_password_hash, check_password_hash
from rdflib import Namespace, Literal, XSD
from rdflib.namespace import RDF
from agentes.agente_perfil_usuario import AgentePerfilUsuario, id_local_valido, recurso_sp
from agentes.agente_recomendacion import AgenteRecomendacion

app = Flask(__name__)
app.secret_key = "smartplay_clave_secreta_2026"

SP = Namespace("http://www.smartplay.com/ontology#")

# ── Rutas de archivos de datos ─────────────────────────────────────────────
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
RUTA_CATALOGO  = os.path.join(BASE_DIR, "datos", "SmartPlay_catalogo.ttl")
RUTA_USUARIOS  = os.path.join(BASE_DIR, "datos", "SmartPlay_usuarios.ttl")
RUTA_INTERACCIONES = os.path.join(BASE_DIR, "datos", "SmartPlay_interacciones.ttl")
RUTA_ONTOLOGIA = os.path.join(BASE_DIR, "datos", "SmartPlay_Ontologia.owl")

# ── Inicialización de agentes ──────────────────────────────────────────────
print("\n" + "="*60)
print("   SmartPlay — Inicializando agentes inteligentes...")
print("="*60)
agente_perfil = AgentePerfilUsuario(
    [RUTA_CATALOGO, RUTA_USUARIOS, RUTA_INTERACCIONES],
    RUTA_ONTOLOGIA,
    ruta_usuarios=RUTA_USUARIOS,
    ruta_interacciones=RUTA_INTERACCIONES,
)
agente_rec    = AgenteRecomendacion(agente_perfil)
print("="*60 + "\n")


# ─────────────────────────────────────────────────────────────────────────────
#  UTILIDADES DE AUTENTICACIÓN
# ─────────────────────────────────────────────────────────────────────────────

def login_required(f):
    """Decorador: redirige a login si no hay sesión activa."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if "usuario_id" not in session:
            flash("Debes iniciar sesión para acceder a esa página.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def _get_password_from_graph(usuario_id: str) -> str | None:
    """Lee el hash de contraseña del grafo RDF para un usuario dado."""
    if not id_local_valido(usuario_id):
        return None
    password_hash = agente_perfil.graph.value(recurso_sp(usuario_id), SP.password)
    return str(password_hash) if password_hash is not None else None


def _add_user_to_graph(usuario_id: str, nombre: str, edad: int,
                       region: str, plataforma: str, password_hash: str):
    """
    Escribe un nuevo usuario en el grafo RDF (memoria) y persiste en disco.
    Reutiliza la lógica de serialización del agente.
    """
    g = agente_perfil.graph
    uri = recurso_sp(usuario_id)

    g.add((uri, RDF.type,           SP.Usuario))
    g.add((uri, SP.nombreUsuario,   Literal(nombre)))
    g.add((uri, SP.edad,            Literal(edad, datatype=XSD.integer)))
    g.add((uri, SP.password,        Literal(password_hash)))
    g.add((uri, SP.region,          Literal(region)))

    plat_uri = SP[plataforma.replace(" ", "")]
    g.add((uri, SP.plataformaPreferida, plat_uri))

    # Persistir
    agente_perfil.guardar()


# ─────────────────────────────────────────────────────────────────────────────
#  RUTAS DE AUTENTICACIÓN
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    if "usuario_id" in session:
        return redirect(url_for("perfil", usuario_id=session["usuario_id"]))

    if request.method == "POST":
        usuario_id = request.form.get("usuario_id", "").strip()
        password   = request.form.get("password", "").strip()

        pwd_hash = _get_password_from_graph(usuario_id)
        if pwd_hash and check_password_hash(pwd_hash, password):
            perfil = agente_perfil.obtener_perfil(usuario_id)
            if not perfil:
                flash("Usuario o contraseña incorrectos.", "danger")
            else:
                session["usuario_id"]    = usuario_id
                session["usuario_nombre"] = perfil.get("nombre", usuario_id)
                flash(f"¡Bienvenido de vuelta, {session['usuario_nombre']}!", "success")
                return redirect(url_for("perfil", usuario_id=usuario_id))
        else:
            flash("Usuario o contraseña incorrectos.", "danger")

    # Pasar lista de IDs para el datalist del formulario
    usuarios = agente_perfil.obtener_todos_usuarios()
    return render_template("login.html", usuarios=usuarios)


@app.route("/logout")
def logout():
    nombre = session.pop("usuario_nombre", "")
    session.pop("usuario_id", None)
    flash(f"Sesión cerrada. ¡Hasta pronto{', ' + nombre if nombre else ''}!", "info")
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        nombre     = request.form.get("nombre", "").strip()
        usuario_id = request.form.get("usuario_id", "").strip()
        edad       = request.form.get("edad", "18").strip()
        region     = request.form.get("region", "Colombia").strip()
        plataforma = request.form.get("plataforma", "PC").strip()
        password   = request.form.get("password", "").strip()
        password2  = request.form.get("password2", "").strip()

        # Validaciones básicas
        if not all([nombre, usuario_id, edad, password]):
            flash("Todos los campos son obligatorios.", "warning")
            return render_template("register.html")

        if not re.match(r'^[A-Za-z][A-Za-z0-9_]{2,19}$', usuario_id):
            flash("El ID de usuario solo puede contener letras, números y _ (3-20 caracteres, debe empezar con letra).", "warning")
            return render_template("register.html")

        if password != password2:
            flash("Las contraseñas no coinciden.", "warning")
            return render_template("register.html")

        if len(password) < 6:
            flash("La contraseña debe tener al menos 6 caracteres.", "warning")
            return render_template("register.html")

        try:
            edad_int = int(edad)
        except ValueError:
            flash("La edad debe ser un número válido.", "warning")
            return render_template("register.html")

        if edad_int < 10 or edad_int > 99:
            flash("La edad debe estar entre 10 y 99 años.", "warning")
            return render_template("register.html")

        # Verificar que el ID no exista ya
        if _get_password_from_graph(usuario_id) or agente_perfil.obtener_perfil(usuario_id):
            flash(f"El ID '{usuario_id}' ya está en uso. Elige otro.", "warning")
            return render_template("register.html")

        # Crear usuario
        pwd_hash = generate_password_hash(password)
        _add_user_to_graph(usuario_id, nombre, edad_int, region, plataforma, pwd_hash)

        session["usuario_id"]     = usuario_id
        session["usuario_nombre"] = nombre
        flash(f"¡Cuenta creada con éxito! Bienvenido, {nombre}.", "success")
        return redirect(url_for("perfil", usuario_id=usuario_id))

    return render_template("register.html")


# ─────────────────────────────────────────────────────────────────────────────
#  RUTAS PRINCIPALES
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Página principal: lista de usuarios. Requiere sesión."""
    if "usuario_id" not in session:
        return redirect(url_for("login"))
    usuarios = agente_perfil.obtener_todos_usuarios()
    total_videojuegos = len(agente_rec._obtener_catalogo())
    total_historiales = len(set(agente_perfil.graph.subjects(RDF.type, SP.EntradaHistorial)))
    return render_template(
        "index.html",
        usuarios=usuarios,
        total_videojuegos=total_videojuegos,
        total_historiales=total_historiales,
    )


@app.route("/perfil/<usuario_id>")
@login_required
def perfil(usuario_id):
    """
    Perfil de un usuario.
    - Cualquier usuario logueado puede VER el perfil de otro.
    - Solo el dueño del perfil puede CALIFICAR juegos.
    """
    if not id_local_valido(usuario_id):
        flash("El ID de usuario solicitado no es válido.", "warning")
        return redirect(url_for("index"))

    datos_perfil = agente_perfil.obtener_perfil(usuario_id)
    if not datos_perfil:
        flash(f"Usuario '{usuario_id}' no encontrado en el grafo.", "danger")
        return redirect(url_for("index"))

    es_propio = (session.get("usuario_id") == usuario_id)

    catalogo      = agente_rec._obtener_catalogo()
    jugados_uris  = agente_perfil.obtener_juegos_jugados_uris(usuario_id)
    juegos_nuevos = sorted(
        [j for j in catalogo.values() if j["uri"] not in jugados_uris],
        key=lambda x: x["titulo"]
    )

    recs = agente_rec.obtener_recomendaciones_guardadas(usuario_id)

    return render_template(
        "perfil.html",
        perfil=datos_perfil,
        juegos_nuevos=juegos_nuevos,
        recomendaciones=recs,
        es_propio=es_propio,
    )


@app.route("/calificar", methods=["POST"])
@login_required
def calificar():
    """
    Solo el usuario logueado puede calificar juegos de su propio perfil.
    """
    usuario_id    = request.form.get("usuario_id", "").strip()
    juego_id      = request.form.get("juego_id", "").strip()
    juego_id = juego_id if id_local_valido(juego_id) else ""
    try:
        calificacion = float(request.form.get("calificacion", 5.0))
        tiempo_jugado = int(request.form.get("tiempo_jugado", 1))
    except ValueError:
        flash("La calificación y las horas deben ser valores numéricos.", "warning")
        return redirect(url_for("perfil", usuario_id=session.get("usuario_id")))
    calificacion = max(1.0, min(calificacion, 10.0))
    tiempo_jugado = max(1, min(tiempo_jugado, 999))

    # Seguridad: solo puede calificar en su propio perfil
    if usuario_id != session.get("usuario_id"):
        flash("No puedes calificar juegos en el perfil de otro usuario.", "danger")
        return redirect(url_for("perfil", usuario_id=usuario_id))

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
@login_required
def recomendar(usuario_id):
    if not id_local_valido(usuario_id):
        flash("El ID de usuario solicitado no es válido.", "warning")
        return redirect(url_for("index"))

    estrategia = request.form.get("estrategia", "")
    if estrategia not in ("", "contenido", "colaborativa", "hibrida"):
        estrategia = ""
    try:
        n = int(request.form.get("n", 5))
    except ValueError:
        n = 5
    n = max(1, min(n, 10))

    recs = agente_rec.recomendar(usuario_id, n=n, estrategia=estrategia)

    if recs:
        flash(f"✓ Se generaron {len(recs)} recomendaciones con estrategia "
              f"'{estrategia or 'automática'}'.", "success")
    else:
        flash("✗ No se pudieron generar recomendaciones para este usuario.", "warning")

    return redirect(url_for("perfil", usuario_id=usuario_id))


@app.route("/api/recomendar/<usuario_id>")
def api_recomendar(usuario_id):
    if not id_local_valido(usuario_id):
        return jsonify({"error": "ID de usuario inválido"}), 400
    estrategia = request.args.get("estrategia", "")
    if estrategia not in ("", "contenido", "colaborativa", "hibrida"):
        estrategia = ""
    try:
        n = int(request.args.get("n", 5))
    except ValueError:
        n = 5
    n = max(1, min(n, 10))
    recs       = agente_rec.recomendar(usuario_id, n=n, estrategia=estrategia)

    for r in recs:
        for campo in ("generos", "plataformas", "mecanicas", "similares"):
            if isinstance(r.get(campo), set):
                r[campo] = list(r[campo])

    return jsonify({
        "usuario":         usuario_id,
        "estrategia":      estrategia or "automática",
        "total":           len(recs),
        "recomendaciones": recs,
    })


@app.route("/api/perfil/<usuario_id>")
def api_perfil(usuario_id):
    if not id_local_valido(usuario_id):
        return jsonify({"error": "ID de usuario inválido"}), 400
    datos = agente_perfil.obtener_perfil(usuario_id)
    if not datos:
        return jsonify({"error": f"Usuario '{usuario_id}' no encontrado"}), 404
    return jsonify(datos)


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=True, port=5000)
