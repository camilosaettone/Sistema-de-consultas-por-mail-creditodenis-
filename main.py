import requests
import imaplib
import smtplib
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import re
import time
import logging
import urllib3
import os
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- Configuración de Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- 1. Configuración vía Variables de Entorno ---
# Railway leerá estos valores de su pestaña "Variables"
ZOHO_EMAIL = os.getenv("ZOHO_EMAIL", "gestiones@creditodenis.com")
ZOHO_PASSWORD = os.getenv("ZOHO_PASSWORD") 
BCRA_API_URL = "https://api.bcra.gob.ar/CentralDeDeudores/v1.0/Deudas/"

# Configuración de Reintentos Automáticos
session = requests.Session()
retries = Retry(total=5, backoff_factor=3, status_forcelist=[500, 502, 503, 504])
session.mount('https://', HTTPAdapter(max_retries=retries))

def consultar_situacion_bcra(cuit_cuil):
    cuit_clean = re.sub(r'\D', '', str(cuit_cuil))
    url = f"{BCRA_API_URL}{cuit_clean}"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Referer': 'https://www.bcra.gob.ar/BCRAyVos/Situacion_Crediticia.asp'
    }

    try:
        logging.info(f"Consultando BCRA para CUIT: {cuit_clean}")
        time.sleep(1) # Delay preventivo
        
        response = session.get(url, headers=headers, timeout=25, verify=False)
        
        if response.status_code == 200:
            data = response.json()
            periodos = data.get('results', {}).get('periodos', [])
            if not periodos: 
                return 1 # Sin deuda registrada
            
            peor_situacion = 1
            for entidad in periodos[0].get('entidades', []):
                sit = int(entidad.get('situacion', 1))
                if sit > peor_situacion: 
                    peor_situacion = sit
            return peor_situacion

        elif response.status_code == 404:
            return 1
        else:
            logging.error(f"Error BCRA Status: {response.status_code}")
            return None

    except Exception as e:
        logging.error(f"Error de conexión BCRA: {e}")
        return None

def conectar_imap():
    try:
        mail = imaplib.IMAP4_SSL("imappro.zoho.com")
        mail.login(ZOHO_EMAIL, ZOHO_PASSWORD)
        return mail
    except Exception as e:
        logging.error(f"Error IMAP: {e}")
        return None

def conectar_smtp():
    try:
        smtp = smtplib.SMTP_SSL("smtppro.zoho.com", 465, timeout=20)
        smtp.login(ZOHO_EMAIL, ZOHO_PASSWORD)
        return smtp
    except Exception as e:
        logging.error(f"Error SMTP: {e}")
        return None

def main():
    logging.info("=== Bot Crédito Denis Iniciado en Railway ===")
    
    if not ZOHO_PASSWORD:
        logging.error("ERROR: No se encontró la variable ZOHO_PASSWORD")
        return

    while True:
        try:
            m = conectar_imap()
            if m:
                m.select('INBOX')
                _, data = m.search(None, 'UNSEEN')
                ids_correos = data[0].split()
                
                if ids_correos:
                    s = conectar_smtp() # Solo conectamos SMTP si hay correos para responder
                    if s:
                        for num in ids_correos:
                            _, msg_data = m.fetch(num, '(RFC822)')
                            msg = email.message_from_bytes(msg_data[0][1])
                            asunto = msg.get('subject', '')
                            remitente = msg.get('from', '')
                            
                            # Busca CUIT en asunto o cuerpo
                            cuerpo = ""
                            if msg.is_multipart():
                                for part in msg.walk():
                                    if part.get_content_type() == "text/plain":
                                        cuerpo = part.get_payload(decode=True).decode()
                            else:
                                cuerpo = msg.get_payload(decode=True).decode()

                            # Regex para capturar CUIT/CUIL
                            match = re.search(r'\b(\d{2}-?\d{8}-?\d{1})\b', asunto + cuerpo)
                            
                            if match:
                                cuit_val = re.sub(r'\D', '', match.group(1))
                                res = consultar_situacion_bcra(cuit_val)
                                
                                if res == 1:
                                    txt, msg_c = "PRE APROBADO", "Situación Normal (Nivel 1). Cumple requisitos base."
                                elif res and res > 1:
                                    txt, msg_c = "RECHAZADO", f"Situación Nivel {res} en BCRA. No apto para crédito."
                                else:
                                    txt, msg_c = "REINTENTAR", "Hubo una demora con el BCRA. Reintentaremos en breve."

                                # Respuesta automática
                                dest = email.utils.parseaddr(remitente)[1]
                                reply = MIMEMultipart()
                                reply['Subject'] = f"RE: {asunto} - {txt}"
                                reply.attach(MIMEText(f"Análisis para CUIT {cuit_val}:\n\nResultado: {txt}\nDetalle: {msg_c}\n\nCrédito Denis - Automatización.", 'plain'))
                                s.sendmail(ZOHO_EMAIL, dest, reply.as_string())
                                logging.info(f"Respuesta enviada a {dest} por CUIT {cuit_val}")
                            
                            # Marcar como leído
                            m.store(num, '+FLAGS', '\\Seen')
                        s.quit()
                m.logout()
        except Exception as e:
            logging.error(f"Error en loop principal: {e}")
        
        time.sleep(60) # Esperar 1 minuto para la próxima revisión

if __name__ == "__main__":
    main()
