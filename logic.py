# logic.py
import io
import json
import re
import time
import os  # Lisätty os-moduuli API-avaimen lukemista varten

import docx
import google.generativeai as genai
import PyPDF2
from groq import Groq  # Uusi Groq-kirjasto
from google.generativeai.types import GenerationConfig

# Alustetaan Groq-client
groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

TEOLOGINEN_PERUSOHJE = (
    "Olet teologinen assistentti. Perusta kaikki vastauksesi ja tulkintasi "
    "ainoastaan sinulle annettuihin KR33/38-raamatunjakeisiin ja käyttäjän "
    "materiaaliin. Vältä nojaamasta tiettyihin teologisiin järjestelmiin ja "
    "pyri tulkitsemaan jakeita koko Raamatun kokonaisilmoituksen valossa."
)


def lataa_raamattu(tiedostonimi="bible.json"):
    """Lataa ja jäsentää Raamattu-datan JSON-tiedostosta."""
    # ... (tämä funktio pysyy ennallaan) ...
    try:
        with open(tiedostonimi, "r", encoding="utf-8") as f:
            bible_data = json.load(f)
    except FileNotFoundError:
        return None
    book_map = {}
    book_name_map = {}
    book_data_map = {}
    book_name_to_id_map = {}
    sorted_book_ids = sorted(bible_data.get("book", {}).keys(), key=int)
    for book_id in sorted_book_ids:
        book_content = bible_data["book"][book_id]
        book_data_map[book_id] = book_content
        info = book_content.get("info", {})
        proper_name = info.get("name", f"Kirja {book_id}")
        book_name_map[book_id] = proper_name
        book_name_to_id_map[proper_name] = int(book_id)
        names = ([info.get("name", ""), info.get("shortname", "")] +
                 info.get("abbr", []))
        for name in names:
            if name:
                key = name.lower().replace(".", "").replace(" ", "")
                if key:
                    book_map[key] = (book_id, book_content)
    sorted_aliases = sorted(
        list(set(alias for alias in book_map if alias)),
        key=len, reverse=True)
    return (
        bible_data, book_map, book_name_map, book_data_map,
        sorted_aliases, book_name_to_id_map
    )


def luo_kanoninen_avain(jae_str, book_name_to_id_map):
    """Luo järjestelyavaimen (kirja, luku, jae) merkkijonosta."""
    # ... (tämä funktio pysyy ennallaan) ...
    match = re.match(r"^(.*?)\s+(\d+):(\d+)", jae_str)
    if not match:
        return (999, 999, 999)
    book_name, chapter, verse = match.groups()
    book_id = book_name_to_id_map.get(book_name.strip(), 999)
    return (book_id, int(chapter), int(verse))


def luo_osio_avain(osion_numero_str):
    """Muuntaa osionumeron (esim. '10.2.1') lajittelua varten."""
    # ... (tämä funktio pysyy ennallaan) ...
    try:
        return [int(part) for part in osion_numero_str.split('.')]
    except (ValueError, AttributeError):
        return [float('inf')]


def erota_jaeviite(jae_kokonainen):
    """Erottaa ja palauttaa jaeviitteen tekoälyä varten."""
    # ... (tämä funktio pysyy ennallaan) ...
    try:
        return jae_kokonainen.split(' - ')[0].strip()
    except IndexError:
        return jae_kokonainen


def lue_ladattu_tiedosto(uploaded_file):
    """Lukee käyttäjän lataaman tiedoston sisällön tekstiksi."""
    # ... (tämä funktio pysyy ennallaan) ...
    if not uploaded_file:
        return ""
    try:
        ext = uploaded_file.name.split(".")[-1].lower()
        bytes_io = io.BytesIO(uploaded_file.getvalue())
        if ext == "pdf":
            return "".join(
                p.extract_text() + "\n"
                for p in PyPDF2.PdfReader(bytes_io).pages
            )
        if ext == "docx":
            return "\n".join(
                p.text for p in docx.Document(bytes_io).paragraphs
            )
        if ext == "txt":
            return uploaded_file.getvalue().decode("utf-8", errors="replace")
    except Exception as e:
        return f"VIRHE TIEDOSTON '{uploaded_file.name}' LUKEMISESSA: {e}"
    return ""


def tee_api_kutsu(prompt, model_name, is_json=False, temperature=0.3):
    """
    Tekee API-kutsun joko Googleen tai Groqiin model_name-perusteella
    ja palauttaa tekstin sekä käyttötiedot.
    """
    try:
        if "gemma" in model_name or "gemini" in model_name:
            # Käytetään Google-clientiä
            safety_settings = [
                {"category": c, "threshold": "BLOCK_NONE"}
                for c in ["HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
                          "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT"]
            ]
            gen_config_params = {"temperature": temperature}
            if is_json:
                gen_config_params["response_mime_type"] = "application/json"

            generation_config = GenerationConfig(**gen_config_params)
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(
                prompt,
                generation_config=generation_config,
                safety_settings=safety_settings
            )
            time.sleep(0.8)
            return response.text, getattr(response, 'usage_metadata', None)
        else:
            # Käytetään Groq-clientiä (Llama, Mixtral, etc.)
            chat_completion = groq_client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=model_name,
                temperature=temperature,
                response_format={"type": "json_object"} if is_json else None,
            )
            response_text = chat_completion.choices[0].message.content
            # Groqin usage-data on eri muodossa, normalisoidaan se
            usage_data = chat_completion.usage
            if usage_data:
                usage_metadata = {
                    'prompt_token_count': usage_data.prompt_tokens,
                    'candidates_token_count': usage_data.completion_tokens,
                    'total_token_count': usage_data.total_tokens
                }
                # Muunnetaan sanakirja objektiksi yhteensopivuuden vuoksi
                return response_text, type('obj', (object,), usage_metadata)()
            return response_text, None

    except Exception as e:
        return f"API-VIRHE: {e}", None


def luo_hakusuunnitelma(pääaihe, syote_teksti):
    """
    Luo älykkään hakusuunnitelman analysoimalla käyttäjän syötettä.
    Käyttää tehokkainta mallia (Gemini 1.5 Pro).
    """
    # ... (tämä funktio pysyy ennallaan, mutta käytetään Gemini Prota laadun vuoksi) ...
    prompt = (
        f"{TEOLOGINEN_PERUSOHJE}\n\n"
        "Tehtäväsi on luoda yksityiskohtainen hakusuunnitelma Raamattu-tutkimusta varten. "
        "Analysoi alla oleva käyttäjän syöte ja noudata ohjeita tarkasti.\n\n"
        f"PÄÄAIHE: {pääaihe}\n\n"
        "KÄYTTÄJÄN SYÖTE:\n---\n{syote_teksti}\n---\n\n"
        "OHJEET:\n"
        "1. **Tarkista ja viimeistele sisällysluettelo:** Lue käyttäjän syötteestä "
        " löytyvä sisällysluettelo ja palauta se loogisena ja selkeänä.\n"
        "2. **Luo kohdennetut hakusanat:** Luo JOKAISELLE sisällysluettelon "
        "osiolle oma, räätälöity lista hakusanoja (5-15 kpl).\n"
        "3. **Palauta vastaus TARKALLEEN seuraavassa JSON-muodossa:**\n\n"
        '{{\n'
        '  "vahvistettu_sisallysluettelo": "1. Otsikko...",\n'
        '  "hakukomennot": {{\n'
        '    "1.": ["avainsana1", "avainsana2"],\n'
        '    "1.1.": ["avainsana3", "avainsana4"]\n'
        '  }}\n'
        '}}\n'
    )
    final_prompt = prompt.format(pääaihe=pääaihe, syote_teksti=syote_teksti)
    vastaus_str, usage = tee_api_kutsu(
        final_prompt, "gemini-1.5-pro-latest", is_json=True, temperature=0.2)

    if not vastaus_str or vastaus_str.startswith("API-VIRHE:"):
        print(f"API-virhe hakusuunnitelman luonnissa: {vastaus_str}")
        return None, usage

    try:
        return json.loads(vastaus_str), usage
    except json.JSONDecodeError:
        print("VIRHE: Hakusuunnitelman JSON-jäsennys epäonnistui.")
        return None, usage


def rikasta_avainsanat(avainsanat, paivita_token_laskuri_callback):
    """Laajentaa avainsanat käyttäen nopeaa Groq-mallia."""
    # ... (tämä funktio pysyy ennallaan, mutta kutsu ohjautuu Groqiin) ...
    prompt = (
        "Olet suomen kielen asiantuntija. Tehtäväsi on laajentaa alla oleva lista "
        "suomenkielisiä avainsanoja. Palauta JSON-objekti, jossa avaimena on "
        "alkuperäinen sana ja arvona on lista, joka sisältää alkuperäisen sanan "
        "sekä 1-3 siihen liittyvää sanaa tai taivutusmuotoa, jotka todennäköisesti "
        "löytyvät Raamatusta (KR33/38).\n\n"
        "Esimerkki:\n"
        '{\n'
        '  "opetuslapseuttaminen": ["opetuslapseuttaminen", "opetuslapsi", "opettaa"],\n'
        '  "hengellinen kypsyys": ["hengellinen kypsyys", "kypsyys", "kasvu"]\n'
        '}\n\n'
        "AVAINSANAT:\n---\n"
        f"{json.dumps(avainsanat, ensure_ascii=False)}\n"
        "---\n\n"
        "VASTAUSOHJE: Palauta VAIN JSON-objekti."
    )
    vastaus_str, usage = tee_api_kutsu(
        prompt, "llama3-8b-8192", is_json=True, temperature=0.1
    )
    paivita_token_laskuri_callback(usage)

    if not vastaus_str or vastaus_str.startswith("API-VIRHE:"):
        print(f"API-virhe avainsanojen rikastamisessa: {vastaus_str}")
        return {sana: [sana] for sana in avainsanat}

    try:
        return json.loads(vastaus_str)
    except json.JSONDecodeError:
        print("VIRHE: Avainsanojen rikastamisen JSON-jäsennys epäonnistui.")
        return {sana: [sana] for sana in avainsanat}


def etsi_ja_laajenna(book_data_map, book_name_map, sana, ennen, jälkeen):
    """Etsii sanaa koko Raamatusta ja laajentaa osumia mekaanisesti."""
    # ... (tämä funktio pysyy ennallaan) ...
    loydetyt_jakeet = set()
    try:
        pattern = re.compile(re.escape(sana), re.IGNORECASE)
    except re.error:
        return set()

    for book_id, book_content in book_data_map.items():
        oikea_nimi = book_name_map.get(book_id, "")
        for luku_nro, luku_data in book_content.get("chapter", {}).items():
            for jae_nro, jae_data in luku_data.get("verse", {}).items():
                if pattern.search(jae_data.get("text", "")):
                    for i in range(int(jae_nro) - ennen,
                                   int(jae_nro) + jälkeen + 1):
                        try:
                            jae_teksti = book_data_map[book_id]["chapter"][
                                luku_nro]["verse"][str(i)]["text"]
                            loydetyt_jakeet.add(
                                f"{oikea_nimi} {luku_nro}:{i} - {jae_teksti}"
                            )
                        except KeyError:
                            continue
    return loydetyt_jakeet


def valitse_relevantti_konteksti(kontekstijakeet, osion_teema):
    """Käyttää nopeaa Groq-mallia kontekstin valintaan."""
    # ... (tämä funktio pysyy ennallaan, mutta kutsu ohjautuu Groqiin) ...
    prompt = (
        "Tehtävä: Olet teologinen asiantuntija. Valitse alla olevasta "
        "jaelistasta VAIN ne jakeet, jotka ovat temaattisesti relevantteja "
        f"aiheeseen: '{osion_teema}'.\n\n"
        "JAE-LISTA:\n---\n"
        f"{kontekstijakeet}\n"
        "---\n\n"
        "VASTAUSOHJE: Palauta VAIN relevanttien jakeiden TÄYDELLISET, "
        "muuttumattomat merkkijonot, kukin omalla rivillään. "
        "Älä lisää numerointeja, selityksiä tai mitään muuta."
    )
    vastaus_str, usage = tee_api_kutsu(
        prompt, "llama3-8b-8192", temperature=0.0)

    if not vastaus_str or vastaus_str.startswith("API-VIRHE:"):
        print(f"API-virhe kontekstin valinnassa: {vastaus_str}")
        return [], usage

    alkuperaiset_set = set(kontekstijakeet.strip().split('\n'))
    palautetut_set = set(vastaus_str.strip().split('\n'))
    return list(alkuperaiset_set.intersection(palautetut_set)), usage


def pisteyta_ja_jarjestele(
        aihe, sisallysluettelo, osio_kohtaiset_jakeet,
        paivita_token_laskuri_callback, progress_callback=None):
    """Pisteyttää ja järjestelee jakeet käyttäen tehokasta Groq-mallia."""
    # ... (tämä funktio pysyy ennallaan, mutta kutsu ohjautuu Groqiin) ...
    final_jae_kartta = {}
    osiot = {
        match.group(1): match.group(3)
        for rivi in sisallysluettelo.split("\n") if rivi.strip()
        and (match := re.match(r"^\s*(\d+(\.\d+)*)\.?\s*(.*)", rivi.strip()))
    }

    total_steps = len(osio_kohtaiset_jakeet)
    current_step = 0

    for osio_nro, jakeet in osio_kohtaiset_jakeet.items():
        current_step += 1
        osion_teema = osiot.get(osio_nro.strip('.'), "")
        if not jakeet or not osion_teema:
            final_jae_kartta[osio_nro] = {
                "relevantimmat": [], "vahemman_relevantit": []}
            continue

        if progress_callback:
            progress_text = f"Järjestellään osiota {osio_nro}: {osion_teema}..."
            progress_percent = int((current_step / total_steps) * 100)
            progress_callback(progress_percent, progress_text)

        jae_viitteet = [erota_jaeviite(j) for j in jakeet]
        prompt = (
            "Olet teologinen asiantuntija. Pisteytä jokainen alla oleva "
            f"Raamatun jae asteikolla 1-10 sen mukaan, kuinka relevantti se on "
            f"seuraavaan teemaan: '{osion_teema}'. Ota huomioon myös tutkimuksen "
            f"pääaihe: '{aihe}'.\n\n"
            "ARVIOITAVAT JAKEET:\n---\n"
            f"{'\\n'.join(jae_viitteet)}\n"
            "---\n\n"
            "VASTAUSOHJE: Palauta VAIN JSON-objekti, jossa avaimina ovat "
            "jaeviitteet ja arvoina kokonaisluvut 1-10. Esimerkki:\n"
            '{\n  "1. Mooseksen kirja 1:1": 8,\n  "Roomalaiskirje 3:23": 10\n}'
        )

        vastaus_str, usage = tee_api_kutsu(
            prompt, "llama3-70b-8192", is_json=True, temperature=0.1)
        paivita_token_laskuri_callback(usage)

        pisteet = {}
        if vastaus_str and not vastaus_str.startswith("API-VIRHE:"):
            try:
                pisteet = json.loads(vastaus_str)
            except json.JSONDecodeError:
                print(f"JSON-jäsennysvirhe osiolle {osio_nro}.")

        relevantimmat = [
            j for j in jakeet
            if int(pisteet.get(erota_jaeviite(j), 0)) >= 7
        ]
        vahemman_relevantit = [
            j for j in jakeet
            if 4 <= int(pisteet.get(erota_jaeviite(j), 0)) <= 6
        ]

        final_jae_kartta[osio_nro] = {
            "relevantimmat": relevantimmat,
            "vahemman_relevantit": vahemman_relevantit
        }

    return final_jae_kartta