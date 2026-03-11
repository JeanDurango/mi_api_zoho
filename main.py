from fastapi import FastAPI, HTTPException
import requests
import pyodbc
import os
from dotenv import load_dotenv
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler

load_dotenv()

app = FastAPI()

# =========================
# CONFIGURACIÓN ZOHO
# =========================
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("REFRESH_TOKEN")

ZOHO_TOKEN_URL = "https://accounts.zoho.com/oauth/v2/token"
ZOHO_CRM_URL = "https://www.zohoapis.com/crm/v2/Tasks"

# =========================
# CONFIGURACIÓN SQL SERVER
# =========================
DB_DRIVER = os.getenv("DB_DRIVER")
DB_SERVER = os.getenv("DB_SERVER")
DB_DATABASE = os.getenv("DB_DATABASE")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_TRUSTED_CONNECTION = os.getenv("DB_TRUSTED_CONNECTION")


def get_db_connection():
    connection_string = (
        f"DRIVER={{{DB_DRIVER}}};"
        f"SERVER={DB_SERVER};"
        f"DATABASE={DB_DATABASE};"
        f"UID={DB_USER};"
        f"PWD={DB_PASSWORD};"
        f"Trusted_Connection={DB_TRUSTED_CONNECTION};"
    )
    return pyodbc.connect(connection_string)


# =========================
# OBTENER ACCESS TOKEN
# =========================
def get_access_token():

    payload = {
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN
    }

    response = requests.post(ZOHO_TOKEN_URL, data=payload)

    if response.status_code != 200:
        raise HTTPException(status_code=400, detail="Error obteniendo access token")

    return response.json()["access_token"]


# =========================
# SINCRONIZAR CITAS
# =========================
@app.post("/sincronizar-citas")
def sincronizar_citas():

    access_token = get_access_token()

    conn = get_db_connection()
    cursor = conn.cursor()

    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Content-Type": "application/json"
    }

    creados = 0
    actualizados = 0
    errores = 0

    def fecha(valor):
        return str(valor) if valor else None

    try:

        # =====================================================
        # CREAR CITAS
        # =====================================================
        cursor.execute("""
            SELECT *
            FROM DWH_Omnivida.citas.citas_nuevas
            WHERE estado_envio = 'PENDIENTE'
        """)

        columnas = [column[0] for column in cursor.description]
        registros = cursor.fetchall()

        for row in registros:

            r = dict(zip(columnas, row))
            consecutivo = r.get("consecutivo")

            try:

                payload = {
                    "data": [
                        {
                            "$se_module": r.get("modulo"),
                            "N_mero_de_documento": r.get("Número de identificación"),
                            "What_Id": r.get("id de registro"),
                            "Subject": r.get("Asunto"),
                            "Status": r.get("Estado"),
                            "ID_cita": r.get("ID cita"),
                            "Fecha_Cita_de_Aplicaci_n": fecha(r.get("Fecha Cita de Aplicación Asignada")),
                            "Fecha_Fecha_ltima_Aplicaci_n_Efectiva": fecha(r.get("Fecha última Aplicación Efectiva")),
                            "Fecha_Pr_xima_Cita_Aplicaci_n": fecha(r.get("Fecha Próxima Cita Aplicación")),
                            "Estado_de_Cita": r.get("Estado de Cita"),
                            "Sede_de_aplicaci_n": r.get("Sede de aplicación"),
                            "Fecha_de_solicitud_de_la_cita_medica": fecha(r.get("Fecha de solicitud de la cita medica")),
                            "Aplica_medicamento": r.get("Aplica medicamento")
                        }
                    ]
                }

                response = requests.post(ZOHO_CRM_URL, json=payload, headers=headers)

                if response.status_code == 201:

                    zoho_id = response.json()["data"][0]["details"]["id"]

                    cursor.execute("""
                        UPDATE DWH_Omnivida.citas.citas_nuevas
                        SET estado_envio = 'ENVIADO',
                            zoho_task_id = ?,
                            fecha_envio = ?,
                            mensaje_error = NULL
                        WHERE consecutivo = ?
                    """, zoho_id, datetime.now(), consecutivo)

                    conn.commit()
                    creados += 1

                else:
                    raise Exception(response.text)

            except Exception as e:

                cursor.execute("""
                    UPDATE DWH_Omnivida.citas.citas_nuevas
                    SET estado_envio = 'ERROR',
                        mensaje_error = ?
                    WHERE consecutivo = ?
                """, str(e), consecutivo)

                conn.commit()
                errores += 1

        # =====================================================
        # ACTUALIZAR CITAS
        # =====================================================
        cursor.execute("""
            SELECT *
            FROM DWH_Omnivida.citas.citas_atendidas_o_inasistidas
            WHERE estado_envio = 'ACTUALIZAR'
        """)

        columnas = [column[0] for column in cursor.description]
        registros = cursor.fetchall()

        for row in registros:

            r = dict(zip(columnas, row))
            consecutivo = r.get("consecutivo")

            try:

                payload = {
                    "data": [
                        {
                            "id": r.get("Id tarea"),
                            "Status": r.get("Estado"),
                            "Fecha_Cita_de_Aplicaci_n": fecha(r.get("Fecha Cita de Aplicación Asignada")),
                            "Estado_de_Cita": r.get("Estado de Cita"),
                            "Aplica_medicamento": r.get("Aplica medicamento"),
                            "Fecha_Fecha_ltima_Aplicaci_n_Efectiva": fecha(r.get("Fecha Última Aplicación Efectiva")),
                            "Fecha_Pr_xima_Cita_Aplicaci_n": fecha(r.get("Fecha Próxima Cita Aplicación"))
                        }
                    ]
                }

                response = requests.put(ZOHO_CRM_URL, json=payload, headers=headers)

                if response.status_code == 200:

                    cursor.execute("""
                        UPDATE DWH_Omnivida.citas.citas_atendidas_o_inasistidas
                        SET estado_envio = 'ENVIADO',
                            fecha_procesado = ?,
                            mensaje_error = NULL
                        WHERE consecutivo = ?
                    """, datetime.now(), consecutivo)

                    conn.commit()
                    actualizados += 1

                else:
                    raise Exception(response.text)

            except Exception as e:

                cursor.execute("""
                    UPDATE DWH_Omnivida.citas.citas_atendidas_o_inasistidas
                    SET estado_envio = 'ERROR',
                        mensaje_error = ?
                    WHERE consecutivo = ?
                """, str(e), consecutivo)

                conn.commit()
                errores += 1

    finally:

        cursor.close()
        conn.close()

    return {
        "creados": creados,
        "actualizados": actualizados,
        "errores": errores
    }


# =========================
# ENDPOINT DE PRUEBA
# =========================
@app.get("/")
def home():
    return {"status": "API funcionando correctamente"}


# =========================
# EJECUCIÓN AUTOMÁTICA
# =========================
#scheduler = BackgroundScheduler()
#scheduler.add_job(sincronizar_citas, 'interval', minutes=3)
#scheduler.start()