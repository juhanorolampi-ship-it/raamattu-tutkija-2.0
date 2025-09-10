# app.py
import re
from collections import defaultdict
import streamlit as st
import google.generativeai as genai

from logic import (
    lataa_raamattu, luo_kanoninen_avain, lue_ladattu_tiedosto,
    luo_hakusuunnitelma, rikasta_avainsanat, etsi_ja_laajenna,
    valitse_relevantti_konteksti, pisteyta_ja_jarjestele
)

# --- ASETUKSET ---
MAX_HITS = 500  # Salliva kattoraja yleisille sanoille

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
    hinnat = {"flash_input": 0.35, "flash_output": 1.05,
              "pro_input": 3.5, "pro_output": 10.5}
    # Arvioidaan karkea jako mallien käytölle
    input_cost = (token_counts['input'] / 1_000_000) * \
        ((hinnat["flash_input"] + hinnat["pro_input"]) / 2)
    output_cost = (token_counts['output'] / 1_000_000) * \
        ((hinnat["flash_output"] + hinnat["pro_output"]) / 2)
    return f"~${input_cost + output_cost:.4f}"


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
        page_title="Älykäs Raamattu-tutkija 2.0", layout="wide")
    st.title("📖 Älykäs Raamattu-tutkija v.2.5 (Finaali)")

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

    raamattu_data = lataa_raamattu()
    if not raamattu_data:
        st.error("KRIITTINEN VIRHE: Raamatun lataus epäonnistui.")
        st.stop()
    _, _, book_name_map, book_data_map, _, book_name_to_id_map = raamattu_data

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
        # --- KORJATTU KOHTA: Turha muuttuja poistettu ---
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

            with st.spinner("Vaihe 1/4: Analysoidaan rakennetta... (Pro)"):
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
            ["Nopea haku (edullisin)", "Tarkka haku (laadukkain)"],
            index=1,
            help=(
                "**Nopea haku:** Etsii avainsanat ja ottaa mekaanisesti mukaan sitä seuraavan jakeen. Erittäin nopea ja edullinen, mutta tulokset voivat olla epätarkkoja.\n\n"
                "**Tarkka haku:** Käyttää monivaiheista tekoälyprosessia, joka tuottaa laadukkaimman ja kattavimman tuloksen. Hitaampi ja kalliimpi."
            )
        )
        if st.button("Kerää jakeet →", type="primary"):
            st.session_state.suunnitelma["vahvistettu_sisallysluettelo"] = \
                st.session_state.final_sisallysluettelo

            osio_kohtaiset_jakeet = defaultdict(set)
            hakukomennot = st.session_state.suunnitelma["hakukomennot"]

            p_bar = st.progress(0, text="Valmistellaan hakua...")

            if haku_tapa == "Nopea haku (edullisin)":
                p_bar.progress(0.1, text="Kerätään jakeita...")
                for osio, avainsanat in hakukomennot.items():
                    for sana in avainsanat:
                        if sana:
                            jakeet = etsi_ja_laajenna(
                                book_data_map, book_name_map, sana, 0, 1)
                            osio_kohtaiset_jakeet[osio].update(jakeet)
                p_bar.progress(1.0, text="Jakeet kerätty!")

            else:  # Tarkka haku (laadukkain)
                p_bar.progress(0.1, text="Rikastetaan avainsanoja... (Flash)")
                alkuperaiset = sorted(list(set(
                    s for sanat in hakukomennot.values() for s in sanat if s
                )))
                rikastetut_map = rikasta_avainsanat(
                    alkuperaiset, paivita_token_laskuri)

                p_bar.progress(0.25, text="Haetaan jakeita välimuistiin...")
                uniikit_sanat = sorted(list(set(
                    laajennettu for laajennos_lista in rikastetut_map.values()
                    for laajennettu in laajennos_lista
                )))
                haku_cache = {
                    s: etsi_ja_laajenna(book_data_map,
                                       book_name_map, s, 1, 1)
                    for s in uniikit_sanat
                }

                liian_yleiset = {
                    s for s, o in haku_cache.items() if len(o) > MAX_HITS}

                p_bar.progress(
                    0.5, text="Suodatetaan jakeita osioille... (Flash)")
                total_sections = len(hakukomennot)
                for i, (osio, avainsanat) in enumerate(hakukomennot.items()):
                    progress_text = f"Suodatetaan osiolle {osio} ({i+1}/{total_sections})"
                    p_bar.progress(
                        0.5 + (i / total_sections) * 0.5, text=progress_text)

                    for alkup_sana in avainsanat:
                        laajennetut = rikastetut_map.get(
                            alkup_sana, [alkup_sana])
                        osumat_yhteensa = set()
                        for sana in laajennetut:
                            if sana in haku_cache and sana not in liian_yleiset:
                                osumat_yhteensa.update(haku_cache[sana])

                        if osumat_yhteensa:
                            otsikko_match = re.search(
                                r"^{}\.?\s*(.*)".format(
                                    re.escape(osio.strip('.'))),
                                st.session_state.final_sisallysluettelo, re.MULTILINE)
                            teema = otsikko_match.group(
                                1) if otsikko_match else ""

                            relevantit, usage = valitse_relevantti_konteksti(
                                "\n".join(sorted(list(osumat_yhteensa))), teema)
                            paivita_token_laskuri(usage)
                            osio_kohtaiset_jakeet[osio].update(
                                relevantit)

                p_bar.progress(1.0, text="Jakeet kerätty!")

            st.session_state.osio_kohtaiset_jakeet = {
                k: sorted(list(v), key=lambda j: luo_kanoninen_avain(
                    j, book_name_to_id_map))
                for k, v in osio_kohtaiset_jakeet.items()
            }
            st.session_state.step = "review_verses"
            st.rerun()

    elif st.session_state.step == "review_verses":
        st.header("Vaihe 3: Tarkista ja muokkaa kerättyä aineistoa")
        kaikki_jakeet = set()
        for jakeet in st.session_state.osio_kohtaiset_jakeet.values():
            kaikki_jakeet.update(jakeet)
        st.info(
            f"Yhteensä uniikkeja jakeita löydetty: {len(kaikki_jakeet)} kpl")

        st.text_area(
            "Voit poistaa tai lisätä jakeita manuaalisesti ennen lopullista järjestelyä:",
            value="\n".join(sorted(
                list(kaikki_jakeet),
                key=lambda j: luo_kanoninen_avain(j, book_name_to_id_map))),
            height=400,
            key="final_verses_str"
        )
        if st.button("Järjestele ja viimeistele →", type="primary"):
            muokatut = set(
                st.session_state.final_verses_str.strip().split("\n"))
            alkuperaiset = st.session_state.osio_kohtaiset_jakeet
            st.session_state.osio_kohtaiset_jakeet = {
                osio: [j for j in jakeet if j in muokatut]
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

            with st.spinner("Vaihe 4/4: Järjestellään ja pisteytetään jakeita... (Flash)"):
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
        sorted_osiot = sorted(jae_kartta.items(
        ), key=lambda item: [int(p) for p in item[0].strip('.').split('.')])

        for osio_nro, data in sorted_osiot:
            otsikko_match = re.search(
                r"^{}\.?\s*(.*)".format(re.escape(osio_nro.strip('.'))),
                sisallysluettelo, re.MULTILINE)
            otsikko = otsikko_match.group(
                1) if otsikko_match else f"Osio {osio_nro}"

            taso = osio_nro.count('.') + 2
            lopputulos += f"{'#' * taso} {osio_nro} {otsikko}\n\n"

            rel = data.get("relevantimmat", [])
            v_rel = data.get("vahemman_relevantit", [])

            if not rel and not v_rel:
                lopputulos += "*Ei löytynyt jakeita tähän osioon.*\n\n"
            if rel:
                lopputulos += "**Relevantimmat jakeet:**\n" + \
                    "".join(f"- {j}\n" for j in rel) + "\n"
            if v_rel:
                lopputulos += "**Vähemmän relevantit jakeet:**\n" + \
                    "".join(f"- {j}\n" for j in v_rel) + "\n"

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