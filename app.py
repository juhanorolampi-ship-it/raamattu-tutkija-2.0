# app.py
import re
from collections import defaultdict
import streamlit as st
import google.generativeai as genai

from logic import (
    lataa_raamattu, luo_kanoninen_avain, lue_ladattu_tiedosto,
    luo_hakusuunnitelma, validoi_avainsanat_ai, etsi_mekaanisesti,
    suodata_semanttisesti, pisteyta_ja_jarjestele, hae_jae_viitteella
)

# Poistetaan vanhentuneet asetukset (MAX_HITS, jne.)

# --- APUFUNKTIOT ---

def paivita_token_laskuri(usage_metadata):
    """Päivittää sessionin token-laskureita saadun datan perusteella."""
    if not usage_metadata:
        return
    input_tokens = getattr(usage_metadata, 'prompt_token_count', 0)
    output_tokens = getattr(usage_metadata, 'candidates_token_count', 0)
    st.session_state.token_count['input'] += input_tokens
    st.session_state.token_count['output'] += output_tokens
    st.session_state.token_count['total'] = (
        st.session_state.token_count['input'] +
        st.session_state.token_count['output']
    )


def laske_kustannus_arvio(token_counts):
    """Laskee karkean hinta-arvion perustuen token-määriin eri malleille."""
    # Hinnat per miljoona tokenia (syyskuu 2025 oletus)
    # Gemini 1.5 Pro: $3.5 / 1M
    # Groq Llama 3.1 8B: ~$0.07 / 1M
    # Groq Llama 3.3 70B: ~$0.70 / 1M
    
    # Karkea arvio mallien käytön jakautumisesta
    groq_input_cost = (token_counts['input'] / 1_000_000) * (0.07 * 0.5 + 0.70 * 0.5)
    groq_output_cost = (token_counts['output'] / 1_000_000) * (0.07 * 0.5 + 0.70 * 0.5)
    gemini_pro_cost = (20000 / 1_000_000) * 3.5
    
    total_cost = groq_input_cost + groq_output_cost + gemini_pro_cost
    return f"~${total_cost:.4f} (Groq + Gemini)"


def reset_session():
    """Nollaa session ja palaa aloitussivulle."""
    st.session_state.clear()
    st.session_state.step = "input"
    st.rerun()


DEFAULT_INSTRUCTIONS = (
    "LISÄOHJEET:\n"
    "Kirjoita noin 5000 sanan mittainen syvällinen ja laaja opetus annetun "
    "aiheen puitteissa. Käytä vivahteikasta kieltä ja varmista, että "
    "teologiset päätelmät ovat loogisia ja perustuvat ainoastaan "
    "annettuun materiaaliin. Voit hyödyntää syvätutkimus-toimintoa "
    "rikastamaan selityksiäsi, mutta älä tuo mukaan uusia jakeita tai "
    "ulkopuolisia oppijärjestelmiä. Käytä käyttäjän määrittelemää tyyliä, "
    "jos sellainen on annettu. Käytä ehdottomasti vain ainoastaan tässä "
    "aineistossa olevia raamatunjakeita sanatarkasti ilman minkäänlaista "
    "muokkaamista!"
)


# --- SOVELLUKSEN PÄÄLOGIIKKA ---

def main():
    """Sovelluksen pääfunktio, joka ohjaa näkymiä."""
    st.set_page_config(
        page_title="Älykäs Raamattu-tutkija 2.5", layout="wide")
    st.title("📖 Älykäs Raamattu-tutkija v.2.5 (Älykäs Haku)")

   # Määritellään tiedostojen raakalinkit GitHubissa
    URL_BIBLE_JSON = "https://raw.githubusercontent.com/juhanorolampi-ship-it/raamattu-tutkija-2.0/version-2.5/bible.json"
    URL_DICTIONARY_JSON = "https://raw.githubusercontent.com/juhanorolampi-ship-it/raamattu-tutkija-2.0/version-2.5/bible_dictionary.json"

    # Alustukset
    if "step" not in st.session_state:
        st.session_state.step = "input"
    if "token_count" not in st.session_state:
        st.session_state.token_count = {"input": 0, "output": 0, "total": 0}

    try:
        genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
    except (KeyError, FileNotFoundError):
        st.error(
            "API-avainta (GEMINI_API_KEY) ei löydy Streamlitin secreteistä.")
        st.stop()

    raamattu_resurssit = lataa_raamattu(URL_BIBLE_JSON, URL_DICTIONARY_JSON)
    if not raamattu_resurssit:
        st.error(
            "KRIITTINEN VIRHE: Raamatun ja/tai sanakirjan lataus epäonnistui. "
            "Varmista, että tiedostot ovat saatavilla GitHubissa."
        )
        st.stop()

    (
        _, _, book_name_map, book_data_map, _,
        book_name_to_id_map, raamattu_sanakirja
    ) = raamattu_resurssit

    # --- SIVUPALKKI ---
    with st.sidebar:
        st.header("Asetukset")
        st.metric(
            label="Tokenit (Tämä istunto)",
            value=f"{st.session_state.token_count['total']:,}",
            help=laske_kustannus_arvio(st.session_state.token_count)
        )
        st.divider()
        st.button("Aloita uusi tutkimus", on_click=reset_session,
                  type="primary", use_container_width=True)

    # --- SOVELLUKSEN VAIHEET ---

    if st.session_state.step == "input":
        st.header("Vaihe 1: Syötä tutkimuksen aihe ja aineisto")
        st.text_input(
            "Tutkimuksen pääaihe:",
            "Esim: Valheveljet, eksyttäjät ja nuori usko",
            key="pääaihe_input"
        )
        aineisto_input = st.text_area(
            "Aihe ja sisällysluettelo:", "...", height=300)

        ladatut_tiedostot = st.file_uploader(
            "Lataa lisämateriaalia (valinnainen)",
            type=["txt", "pdf", "docx"],
            accept_multiple_files=True
        )

        if st.button("Luo hakusuunnitelma →", type="primary"):
            lisamateriaali = "\n".join(
                [lue_ladattu_tiedosto(f) for f in ladatut_tiedostot])
            yhdistetty_teksti = aineisto_input + "\n\n" + lisamateriaali

            with st.spinner("Vaihe 1/4: Analysoidaan rakennetta... (Gemini Pro)"):
                suunnitelma, usage = luo_hakusuunnitelma(
                    st.session_state.pääaihe_input, yhdistetty_teksti)
                paivita_token_laskuri(usage)

                if suunnitelma:
                    st.session_state.suunnitelma = suunnitelma
                    st.session_state.pääaihe = st.session_state.pääaihe_input
                    st.session_state.step = "review_plan"
                    st.rerun()
                else:
                    st.error("Hakusuunnitelman luonti epäonnistui.")

    elif st.session_state.step == "review_plan":
        st.header("Vaihe 2: Vahvista hakusuunnitelma ja kerää jakeet")
        plan = st.session_state.suunnitelma

        st.text_area(
            "Tekoälyn viimeistelemä sisällysluettelo (voit muokata):",
            value=plan["vahvistettu_sisallysluettelo"],
            height=250,
            key="final_sisallysluettelo"
        )
        haku_tapa = st.radio(
            "Valitse jakeiden keräystapa:",
            ["Yksinkertainen haku", "Älykäs haku (Suositus)"],
            index=1,
            help=(
                "**Yksinkertainen haku:** Tekee nopean mekaanisen haun avainsanoilla. Laaja, mutta voi sisältää epärelevantteja osumia.\n\n"
                "**Älykäs haku:** Käyttää monivaiheista tekoälyprosessia (esihaku + suodatus) tuottaakseen laadukkaimman ja kohdennetuimman tuloksen."
            )
        )
        if st.button("Kerää jakeet →", type="primary"):
            st.session_state.suunnitelma["vahvistettu_sisallysluettelo"] = \
                st.session_state.final_sisallysluettelo

            osio_kohtaiset_jakeet = defaultdict(set)
            hakukomennot = st.session_state.suunnitelma["hakukomennot"]
            p_bar = st.progress(0, text="Valmistellaan...")

            # Vaihe 1.5: Älykäs avainsanojen validointi
            p_bar.progress(0.1, text="Vaihe 1.5: Validoidaan avainsanoja...")
            with st.spinner("Tarkistetaan avainsanojen raamatullisuutta (AI)..."):
                kaikki_avainsanat = list(set(
                    sana for avainsanalista in hakukomennot.values()
                    for sana in avainsanalista
                ))
                hyvaksytyt_sanat_setti = validoi_avainsanat_ai(
                    kaikki_avainsanat, paivita_token_laskuri
                )
                puhdistetut_komennot = {}
                for osio, avainsanat in hakukomennot.items():
                    puhdistetut_komennot[osio] = [
                        s for s in avainsanat if s in hyvaksytyt_sanat_setti
                    ]
            hakukomennot = puhdistetut_komennot

            # Vaihe 2: Jakeiden keräys valitulla tavalla
            p_bar.progress(0.3, text="Vaihe 2: Kerätään jakeita...")
            total_sections = len(hakukomennot)
            for i, (osio_nro, avainsanat) in enumerate(hakukomennot.items()):
                progress_percent = 0.3 + (i / total_sections) * 0.7
                teema_match = re.search(
                    r"^{}\.?\s*(.*)".format(re.escape(osio_nro.strip('.'))),
                    st.session_state.final_sisallysluettelo, re.MULTILINE
                )
                teema = teema_match.group(1).strip() if teema_match else ""
                p_bar.progress(
                    progress_percent,
                    text=f"({i+1}/{total_sections}) Haetaan: {teema}..."
                )
                if not teema or not avainsanat:
                    continue

                kandidaatit = etsi_mekaanisesti(
                    avainsanat, book_data_map, book_name_map
                )

                if haku_tapa == "Älykäs haku (Suositus)" and kandidaatit:
                    valinnat, (usage, _, _) = suodata_semanttisesti(
                        kandidaatit, teema
                    )
                    paivita_token_laskuri(usage)
                    for valinta in valinnat:
                        if not isinstance(valinta, dict):
                            continue
                        viite_str = valinta.get("viite")
                        laajenna = valinta.get("laajenna_kontekstia", False)
                        if not viite_str:
                            continue

                        jae = hae_jae_viitteella(
                            viite_str, book_data_map, book_name_map
                        )
                        if jae:
                            osio_kohtaiset_jakeet[osio_nro].add(jae)
                            if laajenna:
                                match = re.match(r'^(.*?)\s+(\d+):(\d+)', jae)
                                if not match:
                                    continue
                                b_name, ch, v_num = match.groups()
                                for j in range(1, 3):
                                    next_v = hae_jae_viitteella(
                                        f"{b_name} {ch}:{int(v_num) + j}",
                                        book_data_map, book_name_map
                                    )
                                    if next_v:
                                        osio_kohtaiset_jakeet[osio_nro].add(
                                            next_v
                                        )
                elif kandidaatit:  # Yksinkertainen haku
                    osio_kohtaiset_jakeet[osio_nro].update(kandidaatit)

            p_bar.progress(1.0, text="Jakeiden keräys valmis!")
            st.session_state.osio_kohtaiset_jakeet = {
                k: sorted(
                    list(v),
                    key=lambda j: luo_kanoninen_avain(j, book_name_to_id_map)
                )
                for k, v in osio_kohtaiset_jakeet.items()
            }
            st.session_state.step = "review_verses"
            st.rerun()

    elif st.session_state.step == "review_verses":
        st.header("Vaihe 3: Tarkista ja muokkaa kerättyä aineistoa")
        kaikki_jakeet = set()
        for jakeet in st.session_state.osio_kohtaiset_jakeet.values():
            kaikki_jakeet.update(jakeet)
        st.info(f"Yhteensä uniikkeja jakeita löydetty: {len(kaikki_jakeet)} kpl")

        st.text_area(
            "Voit poistaa tai lisätä jakeita manuaalisesti ennen lopullista järjestelyä:",
            value="\n".join(sorted(
                list(kaikki_jakeet),
                key=lambda j: luo_kanoninen_avain(j, book_name_to_id_map))),
            height=400,
            key="final_verses_str"
        )
        if st.button("Järjestele ja viimeistele →", type="primary"):
            muokatut_jakeet_str = st.session_state.final_verses_str.strip()
            muokatut_jakeet = set(line for line in muokatut_jakeet_str.split('\n') if line.strip())

            # Varmistetaan, että osio_kohtaiset_jakeet säilyttää rakenteensa,
            # mutta sisältää vain muokatussa listassa olevat jakeet.
            alkuperaiset = st.session_state.osio_kohtaiset_jakeet
            st.session_state.osio_kohtaiset_jakeet = {
                osio: [j for j in jakeet if j in muokatut_jakeet]
                for osio, jakeet in alkuperaiset.items()
            }
            
            st.session_state.step = "output"
            st.rerun()

    elif st.session_state.step == "output":
        st.header("Vaihe 4: Valmis tutkimusraportti")

        if "suunnitelma" not in st.session_state or "pääaihe" not in st.session_state:
            st.warning("Istunnon data on vanhentunut. Aloita uusi tutkimus.")
            if st.button("Palaa alkuun"):
                reset_session()
            st.stop()

        if "jae_kartta" not in st.session_state:
            progress_bar = st.progress(0, "Valmistellaan...")

            def update_progress(percent, text):
                progress_bar.progress(percent / 100.0, text=text)

            with st.spinner("Vaihe 4/4: Järjestellään ja pisteytetään jakeita... (Groq)"):
                jae_kartta = pisteyta_ja_jarjestele(
                    st.session_state.pääaihe,
                    st.session_state.suunnitelma["vahvistettu_sisallysluettelo"],
                    st.session_state.osio_kohtaiset_jakeet,
                    paivita_token_laskuri,
                    progress_callback=update_progress
                )
                st.session_state.jae_kartta = jae_kartta
                st.rerun()

        jae_kartta = st.session_state.jae_kartta
        lopputulos = f"# {st.session_state.pääaihe}\n\n"
        sisallysluettelo = st.session_state.suunnitelma["vahvistettu_sisallysluettelo"]
        sorted_osiot = sorted(jae_kartta.items(), key=lambda item: [int(p) for p in item[0].strip('.').split('.')])

        for osio_nro, data in sorted_osiot:
            otsikko_match = re.search(
                r"^{}\.?\s*(.*)".format(re.escape(osio_nro.strip('.'))),
                sisallysluettelo, re.MULTILINE)
            otsikko = otsikko_match.group(1) if otsikko_match else f"Osio {osio_nro}"

            taso = osio_nro.count('.') + 2
            lopputulos += f"{'#' * taso} {osio_nro} {otsikko}\n\n"

            rel = data.get("relevantimmat", [])
            v_rel = data.get("vahemman_relevantit", [])

            if not rel and not v_rel:
                lopputulos += "*Ei löytynyt jakeita tähän osioon.*\n\n"
            if rel:
                lopputulos += "**Relevantimmat jakeet:**\n" + "".join(f"- {j}\n" for j in rel) + "\n"
            if v_rel:
                lopputulos += "**Vähemmän relevantit jakeet:**\n" + "".join(f"- {j}\n" for j in v_rel) + "\n"

        st.markdown(lopputulos)

        st.divider()
        st.subheader("Seuraavat askeleet: Jatko-ohjeet tekoälylle")
        st.text_area(
            "Voit muokata alla olevaa ohjetta jatkotoimia varten.",
            value=DEFAULT_INSTRUCTIONS, height=250, key="lisäohjeet_input"
        )

        final_download_str = lopputulos
        final_download_str += "\n---\n\n"
        final_download_str += st.session_state.lisäohjeet_input

        st.download_button(
            "Lataa koko raportti", final_download_str, file_name="tutkimusraportti.txt")


if __name__ == "__main__":
    main()