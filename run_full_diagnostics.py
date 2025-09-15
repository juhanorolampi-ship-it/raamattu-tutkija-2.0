# run_full_diagnostics.py (Versio 2.5)
import os
import logging
import time
import json
import re
from collections import defaultdict
from dotenv import load_dotenv
import google.generativeai as genai

from logic import (
    lataa_raamattu, luo_kanoninen_avain, luo_hakusuunnitelma,
    etsi_mekaanisesti, suodata_semanttisesti, pisteyta_ja_jarjestele
)

LOG_FILENAME = 'full_diagnostics_report_v2.5.txt'
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


def log_header(title):
    """Luo ja tulostaa vakio otsikon lokitiedostoon."""
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
    """Laskee karkean hinta-arvion perustuen token-määriin."""
    groq_input_cost = (
        token_counts['input'] / 1_000_000
    ) * (0.07 * 0.5 + 0.70 * 0.5)
    groq_output_cost = (
        token_counts['output'] / 1_000_000
    ) * (0.07 * 0.5 + 0.70 * 0.5)
    gemini_pro_cost = (20000 / 1_000_000) * 3.5
    total_cost = groq_input_cost + groq_output_cost + gemini_pro_cost
    return f"~${total_cost:.4f} (Groq + Gemini)"


def run_diagnostics():
    """Suorittaa koko diagnostiikka-ajon."""
    total_start_time = time.perf_counter()
    log_header("Raamattu-tutkija 2.5 - DIAGNOSTIIKKA (Sanakirja + Debug-loki)")

    logging.info("\n[ALUSTUS] Ladataan resursseja...")
    raamattu_resurssit = lataa_raamattu()
    if not raamattu_resurssit:
        return
    (
        _, _, book_name_map, book_data_map, _,
        book_name_to_id_map, raamattu_sanakirja
    ) = raamattu_resurssit

    try:
        with open("syote.txt", "r", encoding="utf-8") as f:
            syote_teksti = f.read().strip()
            pääaihe = syote_teksti.splitlines()[0]
        logging.info("Syötetiedosto 'syote.txt' ladattu onnistuneesti.")
    except (FileNotFoundError, IndexError):
        logging.error("KRIITTINEN: 'syote.txt' ei löytynyt tai on tyhjä.")
        return

    log_header("VAIHE 1: Tarkennettu hakusuunnitelma (GEMINI PRO)")
    start_time = time.perf_counter()
    suunnitelma, usage = luo_hakusuunnitelma(pääaihe, syote_teksti)
    paivita_token_laskuri(usage)
    if not suunnitelma:
        logging.error("TESTI KESKEYTETTY: Hakusuunnitelman luonti epäonnistui.")
        return
    logging.info(f"Aikaa kului: {time.perf_counter() - start_time:.2f} sekuntia.")
    logging.info(json.dumps(suunnitelma, indent=2, ensure_ascii=False))

    log_header("VAIHE 2: JAKEIDEN KERÄYS (ESIHAKU + ÄLYKÄS SUODATUS)")
    start_time = time.perf_counter()
    osio_kohtaiset_jakeet = defaultdict(set)
    hakukomennot = suunnitelma["hakukomennot"]

    for i, (osio_nro, avainsanat) in enumerate(hakukomennot.items()):
        teema_match = re.search(
            r"^{}\.?\s*(.*)".format(re.escape(osio_nro.strip('.'))),
            suunnitelma["vahvistettu_sisallysluettelo"], re.MULTILINE
        )
        teema = teema_match.group(1).strip() if teema_match else ""
        if not teema:
            continue

        logging.info(
            f"\n  ({i+1}/{len(hakukomennot)}) Käsitellään osiota {osio_nro}: "
            f"{teema}..."
        )

        alkuperaiset = set(s.lower() for s in avainsanat)
        siivotut_sanat = {s for s in alkuperaiset if s in raamattu_sanakirja}
        poistetut_sanat = alkuperaiset - siivotut_sanat
        if poistetut_sanat:
            logging.info(
                f"    - Poistettiin ei-raamatulliset sanat: "
                f"{', '.join(sorted(list(poistetut_sanat)))}"
            )
        if not siivotut_sanat:
            logging.info("    - Ei raamatullisia avainsanoja jäljellä.")
            continue

        logging.info(
            f"    - Vaihe 2.1: Etsitään mekaanisesti "
            f"{len(siivotut_sanat)} avainsanalla..."
        )
        kandidaatit = etsi_mekaanisesti(
            siivotut_sanat, book_data_map, book_name_map
        )
        logging.info(f"    - Löytyi {len(kandidaatit)} kandidaattijaetta.")

        if kandidaatit:
            logging.info("    - Vaihe 2.2: Suodatetaan semanttisesti Groqilla...")
            (
                suodatetut, (usage, lahetetty_prompt, raaka_vastaus)
            ) = suodata_semanttisesti(kandidaatit, teema)
            paivita_token_laskuri(usage)
            osio_kohtaiset_jakeet[osio_nro].update(suodatetut)
            logging.info(
                f"    - Suodatuksen jälkeen jäljellä {len(suodatetut)} jaetta."
            )

            if len(suodatetut) < 10:
                logging.warning(
                    f"    - VAROITUS: Epäilyttävän vähän tuloksia "
                    f"({len(suodatetut)} kpl). Tulostetaan debug-tiedot."
                )
                logging.info("-" * 20 + " DEBUG-LOKI ALKAA " + "-" * 20)
                logging.info("LÄHETETTY PROMPT:\n" + lahetetty_prompt)
                logging.info("\nSAATU RAAKAVASTAUS:\n" + raaka_vastaus)
                logging.info("-" * 20 + " DEBUG-LOKI PÄÄTTYY " + "-" * 20)

        time.sleep(1.5)

    kaikki_jakeet = set().union(*osio_kohtaiset_jakeet.values())
    logging.info(
        f"\nJakeiden keräys valmis. Aikaa kului: "
        f"{time.perf_counter() - start_time:.2f} sekuntia."
    )
    logging.info(f"Kerättyjä uniikkeja jakeita yhteensä: {len(kaikki_jakeet)} kpl.")

    log_header("VAIHE 3: JAKEIDEN JÄRJESTELY JA PISTEYTYS (GROQ)")
    start_time = time.perf_counter()
    def progress_logger(percent, text):
        logging.info(f"  - Edistyminen: {percent}% - {text}")

    jae_kartta = pisteyta_ja_jarjestele(
        pääaihe,
        suunnitelma["vahvistettu_sisallysluettelo"],
        {k: list(v) for k, v in osio_kohtaiset_jakeet.items()},
        paivita_token_laskuri,
        progress_callback=progress_logger
    )
    logging.info(
        f"Järjestely valmis. Aikaa kului: "
        f"{time.perf_counter() - start_time:.2f} sekuntia."
    )

    log_header("LOPULLISET TULOKSET")
    total_end_time = time.perf_counter()
    uniikit_jarjestellyt, sijoituksia = set(), 0
    if jae_kartta:
        for data in jae_kartta.values():
            uniikit_jarjestellyt.update(data.get('relevantimmat', []))
            uniikit_jarjestellyt.update(data.get('vahemman_relevantit', []))
            sijoituksia += (len(data.get('relevantimmat', [])) +
                            len(data.get('vahemman_relevantit', [])))

    logging.info(
        f"KOKONAISKESTO: {(total_end_time - total_start_time) / 60:.1f} min."
    )
    logging.info(f"Kerätyt jakeet (uniikit): {len(kaikki_jakeet)} kpl")
    logging.info(
        f"Järjestellyt jakeet (uniikit): {len(uniikit_jarjestellyt)} kpl"
    )
    logging.info(f"Sijoituksia osioihin yhteensä: {sijoituksia} kpl")
    logging.info(
        f"\nTOKEN-KULUTUS:\n  - Syöte: {TOKEN_COUNT['input']:,} tokenia\n"
        f"  - Tuotos: {TOKEN_COUNT['output']:,} tokenia\n"
        f"  - Yhteensä: {TOKEN_COUNT['total']:,} tokenia"
    )
    logging.info(f"  - Kustannusarvio: {laske_kustannus_arvio(TOKEN_COUNT)}")

    log_header("YKSITYISKOHTAINEN JAEJAOTTELU")
    if jae_kartta:
        sorted_jae_kartta = sorted(
            jae_kartta.items(),
            key=lambda item: [int(p) for p in item[0].strip('.').split('.')]
        )
        for osio, data in sorted_jae_kartta:
            rel, v_rel = data.get('relevantimmat', []), \
                data.get('vahemman_relevantit', [])
            logging.info(
                f"\n--- Osio {osio} (Yhteensä: {len(rel) + len(v_rel)}) ---"
            )
            if not rel and not v_rel:
                logging.info("  - Ei jakeita tähän osioon.")
                continue
            if rel:
                logging.info(f"  --- Relevantimmat ({len(rel)} jaetta) ---")
                for jae in sorted(
                    rel, key=lambda j: luo_kanoninen_avain(j, book_name_to_id_map)
                ):
                    logging.info(f"    - {jae}")
            if v_rel:
                logging.info(
                    f"  --- Vähemmän relevantit ({len(v_rel)} jaetta) ---"
                )
                for jae in sorted(
                    v_rel, key=lambda j: luo_kanoninen_avain(j, book_name_to_id_map)
                ):
                    logging.info(f"    - {jae}")


if __name__ == "__main__":
    load_dotenv()
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    run_diagnostics()
    log_header("DIAGNOSTIIKKA VALMIS")