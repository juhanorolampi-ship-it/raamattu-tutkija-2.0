# create_dictionary.py
import json
import re

print("Aloitetaan Raamattu-sanakirjan luominen...")

try:
    with open("bible.json", "r", encoding="utf-8") as f:
        bible_data = json.load(f)
except FileNotFoundError:
    print("VIRHE: bible.json-tiedostoa ei löytynyt. Aja tämä skripti samassa kansiossa.")
    exit()

all_words = set()

# Käydään läpi koko Raamattu ja kerätään uniikit sanat
for book_id in bible_data.get("book", {}):
    book_content = bible_data["book"][book_id]
    for luku_nro in book_content.get("chapter", {}):
        luku_data = book_content["chapter"][luku_nro]
        for jae_nro in luku_data.get("verse", {}):
            jae_teksti = luku_data["verse"][jae_nro].get("text", "")
            # Puhdistetaan ja lisätään sanat set-rakenteeseen
            words = re.findall(r'\b\w+\b', jae_teksti.lower())
            all_words.update(words)

# Tallennetaan sanat JSON-tiedostoon
try:
    with open("bible_dictionary.json", "w", encoding="utf-8") as f:
        # Muutetaan set listaksi serialisointia varten
        json.dump(list(all_words), f, ensure_ascii=False, indent=2)
    print(f"Valmis! Sanakirja luotu onnistuneesti tiedostoon 'bible_dictionary.json'.")
    print(f"Löytyi {len(all_words)} uniikkia sanaa.")
except Exception as e:
    print(f"VIRHE tiedoston tallennuksessa: {e}")