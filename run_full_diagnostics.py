# run_full_diagnostics.py
import os
import logging
import time
import json
import re
from collections import defaultdict
import google.generativeai as genai
from dotenv import load_dotenv

from logic import (
    lataa_raamattu, luo_kanoninen_avain, luo_hakusuunnitelma, rikasta_avainsanat,
    etsi_ja_laajenna, valitse_relevantti_konteksti, pisteyta_ja_jarjestele
)

# --- ASETUKSET ---
MAX_HITS = 500

# --- LOKITIEDOSTON MÄÄRITYS ---
LOG_FILENAME = 'full_diagnostics_report_GROQ.txt'
if os.path.exists(LOG_FILENAME):
    os.remove(LOG_FILENAME)

logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    handlers=[
        logging.FileHandler(LOG_FILENAME, encoding='utf-8'),
        logging.StreamHandler()
    ]
)

# --- APUFUNKTIOT ---


def log_header(title):
    """Tulostaa selkeän otsikon lokiin."""
    logging.info("\n" + "=" * 80)
    logging.info(f"--- {title.upper()} ---")
    logging.info("=" * 80)


TOKEN_COUNT = {"input": 0, "output": 0, "total": 0}


def paivita_token_laskuri(usage_metadata):
    """Päivittää globaalia token-laskuria."""
    if not usage_metadata:
        return
    input_tokens = getattr(usage_metadata, 'prompt_token_count', 0)
    output_tokens = getattr(usage_metadata, 'candidates_token_count', 0)
    TOKEN_COUNT['input'] += input_tokens
    TOKEN_COUNT['output'] += output_tokens
    TOKEN_COUNT['total'] += input_tokens + output_tokens


def laske_kustannus_arvio(token_counts):
    """Laskee hinta-arvion Groqin Llama3-mallien mukaan."""
    hinnat = {"llama3_8b_input": 0.05, "llama3_8b_output": 0.15,
              "llama3_70b_input": 0.59, "llama3_70b_output": 0.79}
    
    # Oletetaan karkea jako: 8b-mallia käytetään enemmän syötteessä (suodatus)
    # ja 70b-mallia enemmän tuotoksessa (pisteytys).
    input_cost = (token_counts['input'] / 1_000_000) * \
        ((hinnat["llama3_8b_input"] * 0.8) + (hinnat["llama3_70b_input"] * 0.2))
    output_cost = (token_counts['output'] / 1_000_000) * \
        ((hinnat["llama3_8b_output"] * 0.8) + (hinnat["llama3_70b_output"] * 0.2))
    
    # Hakusuunnitelma käyttää edelleen Gemini Prota, lisätään sen kertakustannus
    gemini_pro_cost = (10000 / 1_000_000) * 3.5  # Karkea arvio
    
    return f"~${input_cost + output_cost + gemini_pro_cost:.5f} (Groq Llama3)"


# --- DIAGNOSTIIKKA-AJON PÄÄFUNKTIO ---
def run_diagnostics():
    # ... (tämä funktio pysyy samana kuin edellinen täydellinen versio) ...
    total_start_time = time.perf_counter()
    log_header("Raamattu-tutkija 2.0 - DIAGNOSTIIKKA-AJO (GROQ API)")

    # 1. Ladataan resurssit
    logging.info("\n[ALUSTUS] Ladataan resursseja...")
    raamattu_data = lataa_raamattu()
    if not raamattu_data:
        logging.error("KRIITTINEN: Raamatun lataus epäonnistui.")
        return
    _, _, book_name_map, book_data_map, _, book_name_to_id_map = raamattu_data

    try:
        with open("syote.txt", "r", encoding="utf-8") as f:
            syote_teksti = f.read().strip()
            pääaihe = syote_teksti.splitlines()[0]
        logging.info("Syötetiedosto 'syote.txt' ladattu onnistuneesti.")
    except (FileNotFoundError, IndexError):
        logging.error("KRIITTINEN: 'syote.txt' ei löytynyt tai on tyhjä.")
        return

    # VAIHE 1: Hakusuunnitelman luonti (Pidetään Gemini Pro parhaan laadun vuoksi)
    log_header("VAIHE 1: ÄLYKÄS HAKUSUUNNITELMAN LUONTI (GEMINI PRO)")
    start_time = time.perf_counter()
    suunnitelma, usage = luo_hakusuunnitelma(pääaihe, syote_teksti)
    paivita_token_laskuri(usage)
    end_time = time.perf_counter()

    if not suunnitelma:
        logging.error("TESTI KESKEYTETTY: Hakusuunnitelman luonti epäonnistui.")
        return
    logging.info(f"Aikaa kului: {end_time - start_time:.2f} sekuntia.")
    logging.info(json.dumps(suunnitelma, indent=2, ensure_ascii=False))

    # VAIHE 1.5: Avainsanojen rikastaminen (Groq Llama3-8b)
    log_header("VAIHE 1.5: AVAINSANOJEN RIKASTAMINEN (GROQ LLAMA3-8B)")
    start_time = time.perf_counter()
    alkuperaiset_sanat = sorted(list(set(
        sana for avainsanat in suunnitelma["hakukomennot"].values() for sana in avainsanat if sana
    )))
    rikastetut_sanat_map = rikasta_avainsanat(alkuperaiset_sanat, paivita_token_laskuri)
    end_time = time.perf_counter()
    logging.info(f"Aikaa kului: {end_time - start_time:.2f} sekuntia.")
    logging.info(json.dumps(rikastetut_sanat_map, indent=2, ensure_ascii=False))

    # VAIHE 2: Jakeiden keräys (Groq Llama3-8b suodatus)
    log_header("VAIHE 2: JAKEIDEN KERÄYS (-1/+1, RIKASTETTU HAKU)")
    start_time = time.perf_counter()
    osio_kohtaiset_jakeet = defaultdict(set)
    hakukomennot = suunnitelma["hakukomennot"]

    uniikit_laajennetut_sanat = sorted(list(set(
        laajennettu for laajennokset in rikastetut_sanat_map.values() for laajennettu in laajennokset
    )))
    logging.info(f"\nLöytyi {len(uniikit_laajennetut_sanat)} uniikkia laajennettua avainsanaa. Suoritetaan haut...")

    haku_cache = {}
    for sana in uniikit_laajennetut_sanat:
        haku_cache[sana] = etsi_ja_laajenna(book_data_map, book_name_map, sana, 1, 1)
    logging.info("Kaikki haut suoritettu ja tulokset tallennettu välimuistiin.")

    log_header("HAKUSANOJEN TEHOKKUUSRAPORTTI (RAAKAOSUMAT)")
    nolla_osumaa_sanat = []
    liian_yleiset_sanat = []
    for sana, osumat in haku_cache.items():
        if len(osumat) == 0:
            nolla_osumaa_sanat.append(sana)
        elif len(osumat) > MAX_HITS:
            liian_yleiset_sanat.append(f"'{sana}' ({len(osumat)} osumaa)")

    if liian_yleiset_sanat:
        logging.info(f"\nLiian yleiset hakusanat (yli {MAX_HITS} osumaa, suodatettu pois):")
        logging.info(f"  - {', '.join(liian_yleiset_sanat)}")
        for sana_raportti in liian_yleiset_sanat:
            sana = sana_raportti.split("'")[1]
            if sana in haku_cache:
                del haku_cache[sana]

    logging.info("\nKäsitellään osiot ja suodatetaan jakeet (Groq Llama3-8b)...")
    total_sections = len(hakukomennot)
    for i, (osio_nro, alkuperaiset_avainsanat) in enumerate(hakukomennot.items()):
        logging.info(f"  ({i+1}/{total_sections}) Suodatetaan osiolle {osio_nro}...")
        for alkup_sana in alkuperaiset_avainsanat:
            laajennetut_sanat = rikastetut_sanat_map.get(alkup_sana, [alkup_sana])
            osumat_yhteensa = set()
            for sana in laajennetut_sanat:
                if sana in haku_cache:
                    osumat_yhteensa.update(haku_cache[sana])

            if osumat_yhteensa:
                teema_match = re.search(
                    r"^{}\.?\s*(.*)".format(re.escape(osio_nro.strip('.'))),
                    suunnitelma["vahvistettu_sisallysluettelo"],
                    re.MULTILINE
                )
                teema = teema_match.group(1) if teema_match else ""

                relevantit, usage = valitse_relevantti_konteksti(
                    "\n".join(sorted(list(osumat_yhteensa))), teema)
                paivita_token_laskuri(usage)
                if relevantit:
                    osio_kohtaiset_jakeet[osio_nro].update(relevantit)

    kaikki_jakeet = set()
    for jakeet in osio_kohtaiset_jakeet.values():
        kaikki_jakeet.update(jakeet)
    end_time = time.perf_counter()
    logging.info(f"\nJakeiden keräys valmis. Aikaa kului: {end_time - start_time:.2f} sekuntia.")
    logging.info(f"Kerättyjä uniikkeja jakeita yhteensä: {len(kaikki_jakeet)} kpl.")

    # VAIHE 3: Jakeiden järjestely (Groq Llama3-70b)
    log_header("VAIHE 3: JAKEIDEN JÄRJESTELY JA PISTEYTYS (GROQ LLAMA3-70B)")
    start_time = time.perf_counter()
    def progress_logger(percent, text):
        logging.info(f"  - Edistyminen: {percent}% - {text}")

    jae_kartta = pisteyta_ja_jarjestele(
        pääaihe,
        suunnitelma["vahvistettu_sisallysluettelo"],
        osio_kohtaiset_jakeet,
        paivita_token_laskuri,
        progress_callback=progress_logger
    )
    end_time = time.perf_counter()
    logging.info(f"Järjestely valmis. Aikaa kului: {end_time - start_time:.2f} sekuntia.")

    # LOPPUTULOKSET
    log_header("LOPULLISET TULOKSET")
    total_end_time = time.perf_counter()
    uniikit_jarjestellyt = set()
    sijoituksia = 0
    for data in jae_kartta.values():
        uniikit_jarjestellyt.update(data.get('relevantimmat', []))
        uniikit_jarjestellyt.update(data.get('vahemman_relevantit', []))
        sijoituksia += len(data.get('relevantimmat', [])) + len(data.get('vahemman_relevantit', []))

    logging.info(f"KOKONAISKESTO: {(total_end_time - total_start_time) / 60:.1f} minuuttia")
    logging.info(f"Kerätyt jakeet (uniikit): {len(kaikki_jakeet)} kpl")
    logging.info(f"Järjestellyt jakeet (uniikit): {len(uniikit_jarjestellyt)} kpl")
    logging.info(f"Sijoituksia osioihin yhteensä: {sijoituksia} kpl")
    logging.info("\nTOKEN-KULUTUS:")
    logging.info(f"  - Syöte: {TOKEN_COUNT['input']:,} tokenia")
    logging.info(f"  - Tuotos: {TOKEN_COUNT['output']:,} tokenia")
    logging.info(f"  - Yhteensä: {TOKEN_COUNT['total']:,} tokenia")
    logging.info(f"  - Kustannusarvio: {laske_kustannus_arvio(TOKEN_COUNT)}")

    log_header("YKSITYISKOHTAINEN JAEJAOTTELU")
    for osio, data in sorted(jae_kartta.items(), key=lambda item: [int(p) for p in item[0].strip('.').split('.')]):
        rel = data.get('relevantimmat', [])
        v_rel = data.get('vahemman_relevantit', [])
        logging.info(f"\n--- Osio {osio} (Yhteensä: {len(rel) + len(v_rel)}) ---")
        if not rel and not v_rel:
            logging.info("  - Ei jakeita tähän osioon.")
            continue
        if rel:
            logging.info(f"  --- Relevantimmat ({len(rel)} jaetta) ---")
            for jae in sorted(rel, key=lambda j: luo_kanoninen_avain(j, book_name_to_id_map)):
                logging.info(f"    - {jae}")
        if v_rel:
            logging.info(f"  --- Vähemmän relevantit ({len(v_rel)} jaetta) ---")
            for jae in sorted(v_rel, key=lambda j: luo_kanoninen_avain(j, book_name_to_id_map)):
                logging.info(f"    - {jae}")


if __name__ == "__main__":
    load_dotenv()
    # Varmistetaan, että molemmat avaimet ovat saatavilla
    google_api_key = os.getenv("GEMINI_API_KEY")
    groq_api_key = os.getenv("GROQ_API_KEY")
    if not google_api_key or not groq_api_key:
        logging.error(
            "KRIITTINEN VIRHE: GEMINI_API_KEY tai GROQ_API_KEY ei löydy .env-tiedostosta.")
    else:
        genai.configure(api_key=google_api_key)
        run_diagnostics()
        log_header("DIAGNOSTIIKKA VALMIS")
        logging.info(
            f"Täydellinen raportti tallennettu tiedostoon: {LOG_FILENAME}")