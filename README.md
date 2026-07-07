# LLM Scanner

Maci-sõbralik klienttarkvara (Python) kohalike AI keelemudeli-serverite
**avastamiseks ja testimiseks**. Töötab kõigi OpenAI-ühilduvate serveritega:

- **vLLM** (port 8000)
- **SGLang** (port 30000)
- **llama.cpp server** (port 8080)
- **Ollama** (port 11434)
- **TGI** (text-generation-inference)
- **LM Studio** (port 1234), **LocalAI**, **koboldcpp** jne.

Rakendusel on **kaasaegne graafiline aken** (CustomTkinter — tume/hele teema, ei vaja
rasket Qt-installi) ja lisaks boonusena käsurea-liides skriptimiseks.

## Võimalused

- 🔌 **Ühenda serveriga** – sisesta host, port, API võti, vali endpoint (`chat`/`completions`) ja mudel.
- 🧠 **Nutikas host-väli** – Host-väli aktsepteerib paljast hostinime (`apirouter.itteam.eu`), `host:port`
  kuju või täis-URL-i (`https://host/v1`). Skeem (http/https), port ja võimalik tee-eesliide
  tuletatakse automaatselt: avalik domeen läheb HTTPS-i (443), kohalik IP/localhost HTTP-le
  (pordi-väljalt). Vt [Nutikas host-väli](#nutikas-host-väli).
- 🔎 **Avasta server** – "Detect server" proovib ise mitu kandidaati läbi (https→http) ja valib esimese,
  mis vastab; tuvastab serveri tüübi (vLLM/SGLang/Ollama/…), loetleb mudelid ja kirjutab lahendatud
  URL-i host-väljale tagasi.
- 🌐 **Võrguskann** – skannib kohaliku alamvõrgu (nt `192.168.1.0/24`) ja leiab töötavad LLM-serverid.
- 🎯 **Optimum finder** – eraldi tab, mis **automaatselt leiab optimaalse paralleelsuse ja suurima
  töötava päringusuuruse**. Vt [Optimum finder](#optimum-finder).
- ⏳ **Soak-test** – hoiab fikseeritud koormust N minutit ja mõõdab **püsivat tokenit sisse/välja
  tunnis** (+ kas läbilaskevõime püsib stabiilne). Toetab **TheEye päris-koormuse** kordamist. Vt [Soak-test](#soak-test).
- 🚀 **Capacity** – eraldi tab, mis **tõstab concurrency't astmeliselt (1→2→4→…)** ja leiab **tipp-püsiva
  tokenit/minutis** — endpoint'i võimsuse **lae** ja küllastuspunkti. Valikuline **Target tok/min →
  PASS/FAIL**. Vt [Capacity](#capacity-tokmin-lagi).
- 🧪 **Model fit (Openclaw / Hermes)** – eraldi tab, mis hindab **kas mudel sobib agentseks
  kasutuseks**: Hermes tööriista-kutsed, struktuurne JSON, juhiste järgimine → verdikt
  SOBIB / PIIRIPEAL / EI SOBI. Vt [Model fit](#model-fit-openclaw--hermes).
- 🔌 **Provider fit (OpenRouter / HuggingFace)** – eraldi tab, mis kontrollib **kas backend kannatab
  päris router-liiklust**: API-lepingu vastavus (voogedastus, usage-arvestus, max_tokens/stop,
  determinism, sampling-parameetrid, puhtad veakoodid) + paralleelsuse sweep, mis leiab
  läbilaskevõime **põlve ja esimese pudelikaela** (prefill/järjekord, dekodeerimine, batching, admission
  control). Verdikt SOBIB / PIIRIPEAL / EI SOBI kummalegi pakkujale. Vt [Provider fit](#provider-fit-openrouter--huggingface).
- 🧩 **Capabilities** – eraldi tab, mis **avastab, mis funktsionaalsust endpoint/mudel pakub**:
  API-marsruudid (**embeddings**, rerank, tokenize, moderations, audio, images) ja chat-funktsioonid
  (voogedastus, tööriista-kutsed, JSON-režiim, vision, logprobs, seed, reasoning). Iga rida =
  üks kiire proov → ✓ / ✗ / ~ / n/a. Vt [Capabilities](#capabilities-mida-endpoint-pakub).
- 🚄 **Embed speed** – eraldi tab, mis **mõõdab embedding-mudeli läbilaskevõimet ja kiirust**:
  hoiab batch-koormust ja raporteerib **embeddings/s, sisend-tokenit/s, req/s ja latentsi** (p50/p95).
  Batch-suurus on eraldi nupp (embedding-serverid batch'ivad efektiivselt). Vt [Embed speed](#embed-speed-embeddingu-kiirus).
- 🎯 **Embed quality** – eraldi tab, mis **kontrollib, kas embeddingud päriselt töötavad** (mitte
  kiirust): retrieval-järjestus, parafraas-vs-mitteseotud sim, **eesti↔inglise** cross-lingual,
  vektori omadused (L2-norm, determinism, dim), sisend/batch piirid + rerank-relevantsus. Iga rida
  ✓/✗/~ + numbrid. Vt [Embed quality](#embed-quality-kas-embeddingud-töötavad).
- 📊 **Testid** (Benchmark-tab):
  - **Kiirus** – latentsus (TTFT, aeg esimese tokenini) + läbilaskevõime (dekodeerimise tokenit/s).
  - **Koormustest** – N paralleelset päringut; agregeeritud tok/s ja p50/p95 latentsus.
  - **Kontekst / prefill** – saadab pika ~ctx-pikkuse prompti ja mõõdab prefill-kiirust; kontrollib ka et ctx mahub.
  - **Sanity** – lihtne korrektsuse-kontroll (mitte ainult kiirus).
  - **Concurrency sweep** – käib läbi paralleelsused (nt 1,2,4,8,16) ja leiab küllastuspunkti. Mõõdab
    iga taseme juures nii tok/s kui **latentsuse muutumise** (p50/p95, TTFT); graafik joonistab tok/s
    **või** latentsuse vs paralleelsus (vali tabelis vastav rida).
  - **Prefix cache** – saadab sama pika prefiksi 2× ja mõõdab TTFT kiirenemise → tuvastab automaatse prefix-caching'u (vLLM/SGLang).
  - **Determinism** – sama prompt temp=0 N korda → kui palju % väljunditest on identsed (paljastab batching'u mittedeterminismi).
  - **Limits + recall** – binaarotsinguga tegelik max konteksti pikkus + needle-in-haystack (peidab koodi pikka konteksti ja küsib tagasi).
- 🪵 **Live log** – Benchmark- ja Optimum-tabil voogedastab testi kulgu reaalajas (paremal, lohistatava
  jaoturiga): iga faas ja iga valmiv tulemus koos konkreetsete mõõdiku-ridadega, ajatempliga
  (roheline = OK, punane = viga).
- ⚙️ Seadistatav: max väljund-tokenid, konteksti-tokenid, paralleelsus, päringute arv, **timeout (vaikimisi 95 s)**.
- 💾 **Salvestatud hostid** – salvesta host koos kõigi parameetritega ja **kiirvali** see hiljem rippmenüüst (Connection-tab → "Saved hosts").
- ⌨️ **Host/pordi autocomplete** – varem kasutatud IP-d ja pordid jäävad meelde ja ilmuvad Host- ja Port-välja rippmenüüsse; IP valimisel täidetakse automaatselt selle hosti viimati kasutatud port.
- 📚 **Kõik tulemused jäävad alles** – iga test salvestatakse andmebaasi (`~/.llmscanner/llmscanner.db`). Eraldi **History**-tab näitab kogu ajalugu; Export CSV ja Clear nupud olemas.
- 🔁 **Korduskäivituse võrdlus kõrvuti** – kui käivitad sama hosti + sama konfiga sama testi uuesti, kuvatakse maatriks, kus **jooksud on veergudena** (uusim vasakul: Latest, −1, −2 …) ja mõõdikud ridadena. Keri paremale, et näha vanemaid jookse.
- 🎨 **Mugavad tabelid** – vahelduva reavärviga (sebra); **tulbad on lohistades ümberjärjestatavad**
  (lohista veerupäist) ja **suurendatavad/vähendatavad** (lohista veerupiiri). History-tabelis lisaks
  klikk päisel sordib.
- 📈 **Graafik** – tok/s (vm mõõdik) ajas joonena (kerge Canvas-graafik, ilma lisasõltuvuseta). Benchmark-tabil vali võrdlustabelis rida, et seda joonistada; History-tabil vali tulemuse rida, et näha selle konfigi seeriat ajas.
- ⏯️ **Korda viimast jooksu** – nupp, mis käivitab täpselt sama konfiga viimase benchmarki uuesti (ideaalne seeria kogumiseks graafikule).
- 📤 **Export / Copy** – nii Benchmark- kui Optimum-tabil saab tulemused **CSV-faili salvestada** või
  **lõikelauale kopeerida** (tab-eraldatud, kleebib otse tabelarvutusse) — koos veerupäistega.
- ❓ **Info-ikoonid** – iga seadistuse (kõigil tabidel) kõrval on **ⓘ**, millele klikkides avaneb
  selgitus, mida parameeter teeb ja mida erinevad väärtused annavad.
- ❔ **Abi / Help** – üleval paremal nupp, mis avab juhendi (vahekaartide ülevaade + näpunäited) ning
  **infrastruktuuri (Visioline Infra) ja toe kontaktid** (`support@itteam.eu`).
- 🌗 **Teema** – üleval paremal **Süsteem / Hele / Tume** valik; rakendub kohe ja jäetakse meelde.
- 🌐 **Keel** – **inglise (primary) ja eesti**; valik jäetakse meelde ja rakendub kohe. Tõlgitud on
  vahekaartide nimed, sektsioonide pealkirjad, väljade sildid, nupud ja checkbox'id; ⓘ-abitekstid ja
  pikad kirjeldused jäävad inglise keelde (fallback).
- ⚡ **Koormuse eelseaded** – Connection-tabil **Vestlus / RAG / Agent** nupud täidavad ühe klõpsuga
  mõistlikud parameetrid Benchmark-, Soak- ja Provider-fit-tabidel.
- 🆚 **Risthost/-mudel võrdlus** – History-tabil vali mitu rida (Cmd/Shift-klõps) → **"Võrdle valitud"**
  kõrvutine tabel (mõõdikud × jooksud) üle erinevate serverite/mudelite/konfigide.
- 🧾 **Jagatav raport** – **"Ekspordi raport"** salvestab valitud jooksu(d) Markdown/HTML-failina
  (metaandmed + mõõdikute tabel; mitu valikut → võrdlustabel).
- 🔔 **Valmimise-teavitused ja kiirklahvid** – macOS notification + heli, kui pikk test lõpeb;
  Cmd+R (käivita aktiivse tabi test), Cmd+. / Esc (peata), Cmd+D (detect), Cmd+L (mudelid).
- 🖥️ **Aken sobitub ekraaniga** – akna (ja Abi-/Võrdlus-akende) suurus arvutatakse ekraani mõõtude
  järgi ja tsentreeritakse, nii et rakendus ei ava end suuremana kui väiksem kuvar.

## Paigaldus (macOS)

Vajalik on Python 3.9+ (testitud 3.13). Tkinter on enamasti Pythoniga kaasas.

```bash
cd ~/llmscanner

# Soovitatav: virtuaalkeskkond
python3 -m venv .venv
source .venv/bin/activate

# GUI jaoks:
pip install httpx customtkinter

# Soovi korral käsureatööriist (vajab rich):
pip install rich

# või paigalda pakett (annab käsud `llmscanner` ja `llmscanner-cli`):
pip install -e '.[cli]'
```

> **Tkinter puudub?** CustomTkinter põhineb Tkinteril. Kui kasutad Homebrew Pythonit,
> jooksuta `brew install python-tk`. python.org installer sisaldab Tk-d juba.

## Käivitamine

**Graafiline rakendus:**

```bash
python -m llmscanner
# või kui pakett on paigaldatud:
llmscanner
# või ilma eelnevate sammudeta (loob venv + install esimesel korral):
./run.sh
```

**Iseseisev macOS executable (Python pole vaja):**

```bash
./build_macos.sh          # ehitab dist/LLMScanner — üks fail, bundleb Pythoni + Tk
open dist/LLMScanner      # käivita (või topeltklõps Finderis)
```

`build_macos.sh` kasutab PyInstalleri `--onefile` režiimi ja koondab customtkinteri
teemad; ikoon genereeritakse programmiliselt (pilte pole vaja kaasa panna).

**Käsurida (boonus):**

```bash
# Skanni kohalik võrk
llmscanner-cli scan
llmscanner-cli scan --subnet 192.168.1.0/24 --ports 8000,8080,30000,11434

# Tuvasta üksik server
llmscanner-cli detect --host 127.0.0.1 --port 8000

# Loetle mudelid
llmscanner-cli models --host 127.0.0.1 --port 8000

# Käivita testid
llmscanner-cli bench --host 127.0.0.1 --port 8000 --tokens 256 --test all
llmscanner-cli bench --host 10.0.0.5 --port 30000 --model my-model \
    --test load --concurrency 16 --requests 64
```

## Nutikas host-väli

Host-välja ei pea enam paljalt IP-ks piirama — süsteem püüab ise aru saada, kuidas serverini jõuda:

| Sisestad | Süsteem kasutab |
|---|---|
| `apirouter.itteam.eu` | `https://apirouter.itteam.eu` (443) — avalik domeen → HTTPS |
| `https://apirouter.itteam.eu/v1` | sama; `/v1` sufiks eemaldatakse automaatselt |
| `apirouter.itteam.eu:9000` | `https://apirouter.itteam.eu:9000` |
| `127.0.0.1` / `localhost` | `http://…:<port-väli>` — kohalik → HTTP + pordi-väli |
| `192.168.1.5:8080` | `http://192.168.1.5:8080` |
| `http://host/api` | reverse-proxy tee-eesliide `/api` säilib (päringud lähevad `/api/v1/...`) |
| `[::1]:8000` | `http://[::1]:8000` — IPv6 sulgudes |

**Reeglid lühidalt:** host-stringis olev skeem/port/tee võidab alati pordi-välja üle. Paljas avalik
domeen läheb vaikimisi HTTPS-i (443) — pordi-väli on mõeldud kohaliku serveri töövooks. Kohalik
IP / `localhost` kasutab HTTP-d pordi-väljalt. Kohalike ise-allkirjastatud TLS-serditega serverite
jaoks on sertifikaadi kontroll välja lülitatud.

**"Detect server"** ei eelda õiget skeemi: see proovib tuletatud kandidaadid järjekorras läbi
(nt `https://host` → `http://host` → `http://host:<port>`) ja valib esimese vastava; leitud URL
kirjutatakse host-väljale tagasi, nii et järgnevad testid kasutavad täpselt seda.

## Optimum finder

Eraldi **Optimum finder** tab on automaatne "tuning"-tööriist: see leiab, **kui palju paralleelseid
päringuid** ja **kui suuri päringuid** mudelile korraga tasub anda. Kasutab Connection-tabi hosti,
mudelit ja timeouti. Töö on jagatud faasidesse (A–B alati, C–E valikulised):

1. **Faas A — max kontekst.** Leiab suurima üksikpäringu, mis õnnestub (kuni valitav lagi, vaikimisi
   65536 tokenit — sea serveri `--max-model-len` järgi). Eelistab serveri teatatud `max_model_len`
   väärtust (vLLM), muidu loeb limiidi veateatest või teeb binaarotsingu — nii saadetakse võimalikult
   vähe ülisuuri päringuid.
2. **Faas B — concurrency sweep.** Käib paralleelsustasemed läbi (nt 1→128) mõõduka kontekstiga ja
   **peatub varakult**, kui läbilaskevõime jõuab platoole või tase hakkab vigu andma. Raporteerib:
   - **peak-throughput** paralleelsuse (max agregeeritud tok/s);
   - **efektiivsuse "knee"** — madalaima paralleelsuse, mis annab veel ≥90% tipust (praktiline optimum,
     sest latentsus on tipust madalam). See on **soovituslik** väärtus.
3. **Faas C — päringusuuruste sweep** (valikuline). Käib läbi valitud päringusuurused (vaikimisi
   **1024–65536** tokenit: `1024,2048,4096,8192,16384,32768,49152,65536`) ja leiab **iga suuruse juures** nii optimaalse
   (peak-throughput) paralleelsuse kui ka suurima paralleelsuse, mis veel töötab. Suurused üle
   tuvastatud max konteksti jäetakse vahele (märgitakse ära). See paljastab **KV-cache kompromissi**:
   suurema päringuga saab vähem korraga (nt `1k → parim c16, max c64;  4k → parim c16, max c16`).

4. **Faas D — genereerimispikkuse sweep** (valikuline, vaikimisi väljas). Käib läbi valitud
   väljundpikkused (vaikimisi **64, 256, 1024** tokenit) **soovitatud paralleelsuse (knee) juures** ja
   näitab, kuidas väljundpikkus mõjutab decode-läbilaskevõimet (out tok/s) ja latentsust. Lülitatav
   märkeruuduga "Sweep generation lengths".
5. **Faas E — workload-profiilid** (valikuline, vaikimisi väljas). Mõõdab fikseeritud (sisend/väljund)
   töökoormusi ühe paralleelsuse juures — järgib standardset vLLM serving-benchmarki: **prompt-heavy
   8000/1000, decode-heavy 1000/8000, balanced 1000/1000** (vaikimisi, muudetav), concurrency **16**.
   Nii saab tulemused **otse võrrelda** avaldatud numbritega (nt NVIDIA DGX Spark / Blackwell / Jetson
   Thor). Lülitatav "Workload profiles". *NB: suure väljundiga profiil (nt 8000) võib aeglasemal
   serveril timeout'i ületada — tõsta siis Timeout.*

Suuruste loend on Optimum-tabil muudetav ("Request sizes (tok)"). Suurused tähistavad
**sisendi (prompti) suurust** — see määrab KV-cache surve; genereeritav vastus hoitakse väiksena, et
mõõta konkreetselt seda, kui palju sellise sisendiga päringuid server korraga suudab.

### "Gen tokens / req" parameeter

See on **max väljund-(vastus-)tokenite arv päringu kohta** — kui pikalt mudel iga päringu peale
genereerib (decode-pikkus). Mõju:

- **Väike** (nt 32–64) → test on kiire, aga suure prompti puhul kulub aeg peamiselt prefill'ile, seega
  **out tok/s** peegeldab decode'i kehvemini.
- **Suur** (nt 512+) → mõõdab **päris decode-kiirust**, aga päringud kestavad kauem (aeglasem test,
  suure paralleelsuse/konteksti juures kergem timeout'i tabada).

Faasides B ja C kasutatakse üht väärtust (välja "Gen tokens / req"). Kui tahad näha **eri
väljundpikkuste mõju**, lülita sisse **Faas D** ("Sweep generation lengths") ja anna loend
("Gen lengths (tok)").

### Kolm läbilaskevõime mõõdikut (in / out / total tok/s)

Kõik kolm on **agregeeritud** kiirused üle sama batchi wall-aja, seega **In + Out = Total** (sama
lahutus, mida kasutab nt vLLM benchmark):

- **In tok/s** = `sisend-(prompt-)tokenid / wall` — kui kiiresti server **sisendit töötleb** (prefill /
  ingest). Suure kontekstiga on see põhinäitaja.
- **Out tok/s** = `väljund-(completion-)tokenid / wall` — kui kiiresti **väljundit toodetakse** (decode).
  Suure prompti + lühikese vastuse puhul näib see väike, sest wall kulub peamiselt prefill'ile —
  see ei ole viga, vaid mõõdik lihtsalt ei arvesta sisend-tokeneid.
- **Total tok/s** = `(sisend + väljund) / wall` — kogu töö. **Finder teeb pingerea (peak/knee) selle
  järgi**, sest see peegeldab serveri tegelikku läbilaskevõimet.

Lisaks (nagu vLLM benchmark):

- **TPOT (ms/token)** = `(latentsus − TTFT) / (väljund-tokenid − 1)` — **aeg väljund-tokeni kohta**
  (esimest välja arvatud), st puhas decode-latentsus. See on **kõige võrreldavam** decode-mõõdik:
  prefill'ist sõltumatu, stabiilne sisendi/väljundi suuruste üleselt. **Nõuab tokenhaaval
  voogedastust** — klient küsib `stream:true` + `Accept: text/event-stream`. Kui gateway siiski
  **puhverdab** vastuse (TTFT = kogu latentsus), pole TPOT mõõdetav ja kuvatakse **"–"** (mitte 0),
  koos hoiatusega soovituses.
- **req/s** = edukad päringud / wall — request throughput.
- **peak out tok/s** = enim väljund-tokeneid ükskõik millises 1-sekundilises aknas (completion-põhine —
  täpne puhverdava serveri puhul; voogedastava serveri puhul võib koos-lõppemine tippu ülehinnata).

**Fikseeritud väljundpikkus (võrreldavuse jaoks):** koormus- ja optimum-testid saadavad `ignore_eos` +
`min_tokens`, nii et **iga päring dekodeerib täpselt `max_tokens` tokenit**. Ilma selleta peatuks mudel
sageli juba ~1 tokeni järel (eriti suure täite-prompti puhul), mis muudaks Out tok/s peaaegu nulliks ja
Total'i lihtsalt prefill-läbilaskevõimeks — read poleks võrreldavad. (Kui server neid välju ei toeta,
langetatakse need automaatselt tagasi.) NB: kuna Total sisaldab sisend-tokeneid, kasvab see loomulikult
konteksti suurusega — eri kontekstisuuruste võrdlemiseks vaata **Out tok/s** või võrdle sama suuruse sees.

> Tokenite arvud tulevad serveri `usage`-väljast (vLLM/SGLang täpne), muidu **hinnatakse** (~4 tähemärki
> tokeni kohta). Kui server jätab `usage` ära (nn vaikne semantiline degradatsioon — parameeter kaob,
> aga vastus on 200 OK), **kõik tok/s numbrid on ligikaudsed** — tööriist märgib sellised read
> **⚠ est tokens** ja lisab soovitusse hoiatuse (veerg `est_frac` ekspordis). Lisaks arvutatakse
> per-päring **decode-kiirus** (väljund / (lõpp − esimene token),
> ilma prefill'ita) — Benchmark-tabi koormustestis real "per-req decode tok/s (mean)".

### Erinevad prefiksid (prefix-affinity ruutimise vältimine)

Mõned load-balancerid (nt **ApiRouter**) kasutavad **prefix-affinity ruutimist**: sarnase/identse
prompt-prefiksiga päringud suunatakse **samale backendile** KV-cache soojuse pärast. Sünteetilise
koormustesti puhul tähendaks see, et kõik "N paralleelset kasutajat" maanduvad **ühele GPU-le** ja
teised seisavad jõude → concurrency saab valesti (liiga madalalt) mõõdetud.

Selle vältimiseks alustab optimum finder iga päringut **unikaalse kõrge-entroopiaga preambuliga**
(~64 juhuslikku tokenit), mis on erinev alates esimesest tokenist ja piisavalt pikk, et katta ruuteri
prefiksi-plokid. Nii näeb ruuter iga päringut **eraldi vestlusena** ja jagab koormuse backendide vahel.

Lülitatav märkeruuduga **"Distinct request prefixes"** (vaikimisi **sees**). Lülita välja, kui tahad
teadlikult mõõta prefix-cache / affinity käitumist (siis lähevad päringud tõenäoliselt ühele
backendile).

### Export CSV / Copy to clipboard

Nupp **"Export CSV…"** salvestab kõik mõõdetud punktid CSV-faili koos **veerupäistega**:
`phase, concurrency, ctx_tokens, gen_tokens, requests, success, in_tok_s, out_tok_s, total_tok_s,
tpot_ms, req_per_s, peak_out_tok_s, lat_p50_s, lat_p95_s, ttft_p95_s, feasible, note`.

Nupp **"Copy to clipboard"** paneb sama tabeli (koos päistega) lõikelauale **tab-eraldatud** kujul, mis
kleebib otse tabelarvutusse (Excel/Sheets) veergudesse.

Iga mõõdetud punkt ilmub reaalajas tabelisse (roheline = feasible, punane = kukkus, tipp/knee esile
tõstetud) ja live-logisse; lõpus kuvatakse **soovituslause** ja tok/s-vs-paralleelsus graafik. Jooksva
testi saab igal ajal **Cancel**-nupuga katkestada (juba mõõdetud read jäävad tabelisse alles).

**Feasible = "kuni (1 − min success %) päringutest tohib ebaõnnestuda"** (vaikimisi 90%). Kui tase
kukub, lõpetatakse sellel teljel kõrgemale ronimine. Kestust piiravad varajane peatamine ja päringu
timeout — ei tehta täielikku 128×256k ristkorrutist.

> ⚠️ Optimum finder on **koormustest**: käivita seda ainult serverite vastu, mida sa **omad või millel
> on luba testida**.

**Settle-paus:** iga mõõtmise ette tehakse paus (vaikimisi **3 s**), et server jõuaks eelmise pursa
maha laadida — vabastada KV-cache, tühjendada järjekord, lasta rate-limit aknal taastuda — enne kui
järgmist concurrency't / suurust / profiili testitakse. Ilma selleta **valgub ühe taseme jääkkoormus
järgmisesse** (nt petlikud "at capacity" 429-d, mis on tegelikult vaid virna laotud päringud). Tõsta
jagatud/rate-limititud gateway jaoks; pane 0 pühendatud kohaliku serveri korral.

Seadistatav: **paralleelsustasemed** (vaikimisi `1,2,4,8,16,24,32,48,64`), päringusuuruste loend
(vaikimisi 1024–65536), genereerimispikkuste loend, workload-profiilid, concurrency-faasi kontekst,
gen-tokenid päringu kohta, päringuid töölise kohta, konteksti lagi, min success %, **settle-paus**, ja
kas teha päringusuuruste / genereerimispikkuse / profiili sweep.

## Soak-test

**Soak**-tab mõõdab **püsivat läbilaskevõimet — kui palju tokeneid sisse ja välja server (koos oma
backendidega) tegelikult tunnis annab** pideva koormuse all. Erinevalt teistest testidest (mis
saadavad fikseeritud arvu päringuid ja lõpetavad) hoiab soak-test **fikseeritud concurrency't kindla
aja jooksul** (nt 30 min) ja saadab pidevalt päringuid.

- `concurrency` töölist saadavad päringuid järjest, nii et **täpselt `concurrency` päringut on kogu aeg
  õhus.**
- Reaalajas kuvatakse: **IN / OUT / TOTAL tok/s** ja nende **tokenit/tunnis** projektsioon
  (`tok/s × 3600`), req/s, TPOT, latents, vead — ning **väljund-tok/s minutis graafik** (kas
  läbilaskevõime **püsib stabiilne** või langeb: termiline throttling, mälu, gateway-tõrked).
- Väljundpikkus forsitakse `ignore_eos`-iga; **suure väljundi puhul tõsta Timeout.**
- Jooksu saab **Stop**-nupuga katkestada (viimased numbrid jäävad nähtavale).

**TheEye workload (valikuline):** fikseeritud päringusuuruse asemel **kordab TheEye päris
produktsiooni-liiklust** — iga päring valib taski (kaalutud päris kutsesageduse järgi:
classification, social_image_understand, extraction, entity_update jne) ja sämpeldab sisend/väljund
tokenite arvu selle taski mõõdetud jaotusest (lognormaalne sobitus keskmise/p95 järgi). Nii saad
**realistliku püsiva tokenit/tunnis** oma tegeliku koormuse jaoks (enamik lühikesed struktureeritud
kutsed ~1,3k sisse / ~150 välja + harv raske entity-genereerimine). Sisend/väljund väljad
ignoreeritakse — muudad ainult **aega ja concurrency't**.

**Overload probe (+10%, vaikimisi sees):** jooksutab **10% üle concurrency limiidi** (nt 64 → 72), et
kontrollida **admission control'i** — kas server lükkab üleliigsed päringud korrektselt tagasi (nagu
OpenRouter / HuggingFace / hästi seadistatud vLLM), või võtab kõik vastu ja degradeerub vaikselt.
Verdikt ütleb, kumb juhtus:
- **✅ 429/503** üleliigsele → korrektne backpressure;
- **⚠ tagasilükkamist pole, aga väljund kärbitud** → admission control puudub;
- **❌ timeout'id / kõvad vead** → server puruneb ülekoormuse all.
Tagasilükkamised (429/503) eristatakse "kõvadest" vigadest (timeout, connection, 500) tabelis eraldi.

**Näide:** concurrency 64, sisend 4000 / väljund 500 tokenit (RAG-tüüpi), 30 min → näed nt
"IN 136 M/h · OUT 7.6 M/h" ja kas see püsis 30 min jooksul.

> **Vali concurrency targalt:** jooksuta enne **Optimum finder**, kasuta selle peak/knee väärtust —
> siis mõõdab soak-test *maksimaalset* püsivat läbilaskevõimet. Ja mäleta, et tokenit/tunnis sõltub
> töökoormuse kujust (sisend/väljund suhe): RAG-koormus annab palju tokenit **sisse**, chat/agentic
> rohkem **välja**.

## Capacity (tok/min lagi)

**Capacity**-tab vastab ühele küsimusele: **kui palju tokeneid minutis see endpoint päriselt suudab?**
See on võimsuse **lagi** — number, mille paned kirja SLA-sse või mahuplaneerimise dokumenti.

Erinevus teistest tabidest:

| Tab | Mida teeb |
|-----|-----------|
| **Optimum finder** | kiire sweep → parim concurrency, hetkeline tipp-tok/s |
| **Soak** | *fikseeritud* concurrency, hoiab 30 min → tokenit/**tunnis** (vastupidavus) |
| **Capacity** | *tõstab* concurrency't astmeliselt → tipp-püsiv tokenit/**minutis** (lagi) |

Kuidas töötab:

- **Ramp:** concurrency käib astmeti **1 → 2 → 4 → 8 → … → Max concurrency**. Igal astmel hoiab test
  selle arvu päringuid pidevalt õhus **"Window / step"** sekundit (vaikimisi 40 s). Vali aken
  **päringu kestusest pikem** (vt latentsi p95 Benchmark-tabilt) — kui ükski päring akna sees valmis
  ei jõua, ütleb test selgelt *"raise Window / step"*.
- **Steady-state mõõtmine:** iga akna esimene ~kolmandik visatakse ära (järjekorra täitumine, külm
  KV-cache), ülejäänu pealt mõõdetakse **IN / OUT / TOTAL tokenit minutis**.
- **Graafik:** küllastuskõver iga taseme kohta — **punased punktid** on üle võimsuse (saturatsioon),
  **roheline rõngas** märgib mõõdetud tippu. Suur readout värvub tulemusega (roheline = võimsus
  leitud / target täidetud, punane = ei).
- **Saturatsiooni tuvastus** — ramp peatub varakult, kui:
  - läbilaskevõime **platoole jõuab** (concurrency lisamine ei tõsta enam tok/min, < 8% kasvu), **või**
  - server hakkab **tagasi lükkama** (429/503 — admission-limiit käes), **või**
  - tekivad **kõvad vead/timeout'id** või **väljund kärbitakse** (dekodeerimine küllastunud).
- **Tulemus:** **tipp-püsiv TOTAL tok/min** (= võimsus), **millisel concurrency'l** see saavutati, ja
  **miks** ramp peatus. Kui ramp jõuab max concurrency'ni ilma peatumata, öeldakse *"still climbing —
  tõsta Max concurrency"* (tegelik lagi on kõrgemal).

**Target tok/min (valikuline):** kui täidad nõutava võimsuse (nt lepingu TPM või tippkoormuse, mida pead
teenindama), lisab tulemus **PASS/FAIL** verdikti — kas mõõdetud tipp-võimsus täidab selle. Tühjaks
jättes lihtsalt mõõdab lae.

**Näide:** Max concurrency 64, sisend 1000 / väljund 500, 40 s/samm → ramp 1→2→4→8→16→32→64, näed nt
"CAPACITY 31.2 M/min @ c=8 · saturatsioon: server hakkas c=32 juures 429-ga tagasi lükkama". Kui panid
Target 5 M/min → **✅ PASS**.

> ⚠️ Nagu Optimum finder ja Soak, on ka Capacity **koormustest** — jooksuta ainult serverite vastu,
> mida sa omad või milleks sul on luba.

## Model fit (agentne sobivus)

Eraldi **Model fit** tab ei mõõda kiirust vaid **võimekust**: kas mudel sobib agentseks
kasutuseks. Jooksutab paarikümne lühikese proovi patarei (determistlik, temperature 0) ja annab
verdikti **SOBIB / PIIRIPEAL / EI SOBI**. Märkeruut **"Lülita thinking testi ajaks välja"**
(vaikimisi sees) testib Qwen3-stiilis reasoning-mudelit agentses režiimis.

Testitavad dimensioonid (igaüks lülitatav, annab 0–100% skoori):

1. **Tööriista-kutsed** — mudelile antakse tööriistad **natiivse OpenAI `tools` API** kaudu
   (get_weather, web_search, calculator, send_email — see, mida routerid/vLLM/TGI/SGLang tegelikult
   kasutavad); mudel, kes tunneb ainult prompt-konventsiooni, saab krediiti ka siis kui emiteerib
   tekstis Hermes-`<tool_call>` ploki. Hinnatakse: kas kutsub tööriista, valib **õige tööriista**,
   täidab **õiged argumendid**, ja — oluline — **ei kutsu tööriista** kui küsimus vajab tavalist
   vastust (valekutsete määr peaks olema 0%). See on põhiline agentne võimekus.
2. **Struktuurne JSON väljund** — mudelilt küsitakse kindlat JSON-kuju ilma proosa/koodiaedadeta;
   hinnatakse kas vastus parse'ub ja vastab nõutud võtmetele/tüüpidele (mis muidu lõhub
   `json.loads()`-pipeline'i).
3. **Juhiste järgimine & formaadidistsipliin** — ranged formaadikäsud (täpselt üks sõna, ainult
   number, kolm rida) + kontroll, et mudel **ei leki mõtlemist / `<think>` tellinguid** nähtavasse
   vastusesse, mida agent peab parse'ima.
4. **Latents & läbilaskevõime** — mõõdab vastuse-latentsi ja väljund-tok/s kõigi proovide peal.

**Verdikt** on kaalutud segu (tööriist 0.5, JSON 0.25, juhised 0.25) + **kõva värav**: kui mudel
ei suuda usaldusväärselt tööriistu kutsuda (valiidsete tool-call'ide määr < 50%), on tulemus
alati **EI SOBI**, ükskõik kui puhas on ülejäänu. Tulemuste tabel näitab iga proovi eraldi
(✓/✗ + detail), et näeksid täpselt, kus mudel komistab.

## Provider fit (OpenRouter / HuggingFace)

Eraldi **Provider fit** tab vastab kahele küsimusele: **kas see backend kannataks päris
OpenRouteri / HuggingFace inference-liiklust**, ja **kus ta esimesena katki läheb**. Kaks faasi:

### 1. API-lepingu vastavus

Kiired üksik-proovid, igaüks vastab ühele kõvale nõudele, mille router serverile esitab (✓/✗):

- **Chat endpoint** — `/v1/chat/completions` tagastab vastuse (OpenAI-ühilduvus).
- **Streaming (SSE)** — vastus tuleb token-haaval (TTFT < koguaeg), mitte puhverdatult. OpenRouter:
  *"stream tokens immediately rather than queueing"*.
- **Usage-arvestus** — server tagastab prompt/completion tokenite arvu. Vajalik täpseks token-põhiseks
  arvestuseks/throughput'iks (routerite lubatud parameeter), kuigi pole rangelt provideri-nõue.
- **max_tokens** — genereerimine peatub limiidil; **finish_reason=length** katkestusel.
- **Stop-järjestused** — `stop` parameetrit järgitakse (OpenRouteri lubatud parameeter).
- **Determinism (temp 0)** — sama prompt annab identse väljundi (greedy dekodeerimine).
- **Sampling-parameetrid** — temperature/top_p/seed **päriselt rakenduvad** (erinev seed → erinev väljund).
- **Concurrent-korrektsus** — paralleelne päringu-puhang õnnestub tervikuna.
- **Puhtad veakoodid** — vigane päring saab 4xx JSON-vea, mitte 5xx / rippumise (OpenRouteri uptime-reeglid:
  400 ei lähe uptime'i vastu, 500+ läheb).
- **Auth-jõustamine** — tahtlikult vale API-võtmega päring saab 401/403. Avatud otspunkt (vale võti
  aktsepteeritud) on lokaalses arenduses OK, aga mitte live-provideri jaoks → mitte-kriitiline värav.
- **Tool calling (native API)** — päris OpenAI `tools`/`tool_choice` API-parameeter (mida OpenRouter/
  vLLM/TGI/SGLang tegelikult kasutavad); loeb vastuse `tool_calls` (streaming + non-stream). **Gate'ib
  nii OpenRouteri kui HuggingFace'i verdikti.** (`/v1/completions` otspunktil n/a — legacy API-l pole
  tools'i.)
- **Tool calling (Hermes prompt)** — **tagavara-kontroll**, mis jookseb AINULT siis, kui natiivne API
  (ülal) ei tööta. Natiivse tööriista-kutsega mudel ei vaja prompt-põhist Hermes/NousResearch
  `<tool_call>` XML-konventsiooni, seega näidatakse siis lihtsalt _"n/a — native tool-calling works"_
  (roheline), mitte segadust tekitavat punast. Kui natiivne kukub, testitakse Hermes-konventsiooni
  (Openclaw/agent-raamistikud) ja ebaõnnestumisel näidatakse mudeli tegelikku vastust. **Ei mõjuta
  verdikti** kummalgi juhul — informatiivne.
- **Structured output** — nõutud JSON-kuju parse'ub + vastab skeemile. **HF testib structured-output'i.**
- **/v1/models metadata** — `context_length` (+ pricing) on avaldatud. Mõlemad routerid loevad neid
  `/v1/models`-ist (OpenRouteri model-spec; HF `:fastest`/`:cheapest` valik).

### 2. Aususe-testid (integrity) — *"kas kontrollimatu kolmas osapool petab meid või kasutajaid"*

Adversariaalsed proovid, mida router ajaks backendi peal, mida ta ise ei halda:

- **Token-loenduse ausus** — sunnib teadaoleva väljundpikkuse (`ignore_eos`) ja võrdleb serveri
  raporteeritud `completion_tokens`-i tekstist tuletatud tokenizer-agnostilise hinnanguga. Kõrge
  suhe = **arve-täitmine (billing inflation)**. See on OpenRouteri jaoks **kõva blokk** (router
  arveldab tokenite järgi → üle-lugemine petab kasutajaid otse).
- **Konteksti-ausus** — peidab koodi pikka prompti (needle-in-haystack) mitmel sügavusel serveri
  reklaamitud limiidi lähedal ja küsib tagasi. Kukub, kui server **vaikselt kärbib** või lubatud
  kontekst pole päris.
- **Mudeli kvaliteet / autentsus** — golden-answer eval (faktid/matemaatika/loogika). Vaikselt
  **kvantitud / vale / katkine mudel** kukub need. *Pole lõplik kvant-detektor, vaid esimene
  kvaliteedi-põrand, mida router enne usaldamist ajaks.*
- **Kliendi-katkestuse käitlus** — mõõdab probe-TTFT, ujutab serveri üle mitme pika päringuga, mis
  **katkestavad ühenduse esimese tokeni järel** (nagu router teeb, kui kasutaja tühistab), ja mõõdab
  probe-TTFT uuesti. Kui server vabastas slotid → kiire; kui jätkas hüljatud päringute genereerimist →
  probe jääb järjekorda. *Informatiivne (ajastus-tundlik), aga suur hüpe on päris ohumärk.*
- **Logprob-fingerprint** — mudeli enesekindlus triviaalsel faktil (proxy täpsusele; informatiivne,
  paljud serverid ei avalda logprobe).

> **🧠 Reasoning-mudelid** (DeepSeek-R1 / Qwen thinking / QwQ jms): kui mudel paneb nähtava vastuse
> peidetud arutlusesse ja väike token-eelarve kulub sellele täielikult, oleks `content` tühi ja
> testid kukuksid valelt. Tööriist **tuvastab reasoning-mudeli mõlemas levinud vormis** — eraldi
> `reasoning_content`/`reasoning` väljana (nt hosted routerid) **ja** otse `content`-is `<think>`
> siltidena (levinud kohalike serverite — vLLM / llama.cpp / SGLang — juures), sh kui token-eelarve
> ei jõua sulgevat `</think>` silti kätte saada (poolelijäänud arutlus loetakse tervikuna
> reasoning'uks, mitte vastuseks). **Loeb reasoning-tokenid token-aususe hulka** (thinking-mudelit ei
> süüdistata inflatsioonis) ja annab korrektsus-/kvaliteedi-proovidele **laiendatud eelarve** +
> eemaldab `<think>`, et jõuda nähtava vastuseni. Raportis on märge "🧠 reasoning model".

### 3. Paralleelsuse sweep — pudelikaela otsing

Käib läbi paralleelsuse tasemed (nt 1,4,8,16,32) realistliku päringukujuga ja mõõdab igal tasemel
väljund-tok/s, TTFT **p95 ja p99** (saba-latents), lõpp-latentsi p99, TPOT (aeg väljund-tokeni kohta),
req/s ja **429/503 vs kõvad vead**. Sellest
tuletatakse:

- **Läbilaskevõime põlv (knee)** — paralleelsus, kus tok/s lakkab kasvamast (jätkusuutlik lagi).
- **Esimene pudelikael** — dominantne signaal:
  - **Prefill / järjekorra-piir** — TTFT p95 plahvatab koormuse all, TPOT püsib → päringud seisavad
    järjekorras (scheduler/prefill on kitsaskoht); läbilaskevõime OK, aga esimese tokeni latents kannatab.
  - **Dekodeerimise-piir** — TPOT tõuseb koormuse all → KV-cache / mäluriba surve dekodeerimis-batchis.
  - **Batching puudub** — tok/s ei skaleeru paralleelsusega (üks päring küllastab juba GPU); halb TGI-stiilis
    läbilaskevõimele.
  - **Katki koormuse all** — kõvad vead / timeout'id puhaste tagasilükete asemel.

**Admission control** hinnatakse eraldi dimensioonina ülekoormuse-proovikust (+25%): puhas tagasilükkamine
(429/503) vs katkiminek vs vaikne neelamine. OpenRouter nõuab sõna-sõnalt *"return early 429s if under
load, rather than queueing requests"*.

**Verdikt** kummalegi pakkujale eraldi (rõhuasetused erinevad):
- **OpenRouter** — streaming, usage, **token-loenduse ausus** (kõva blokk), stop/max_tokens,
  auth-jõustamine, `/v1/models` metadata, **konteksti-ausus + mudeli kvaliteet**, puhtad vead,
  **TTFT p95 ≤ SLA ja p99 ≤ 2×SLA** põlve juures, stabiilsus koormuse all.
- **HuggingFace / TGI** — streaming, concurrency, `/v1/models` metadata, **TTFT < 5 s** (HF dokumenteeritud
  lävi, single-call streaming), **tool-calling + structured-output** (HF valideerib mõlemat),
  **konteksti-ausus + mudeli kvaliteet**, ja läbilaskevõime — **batching peab skaleeruma (≥1.5×)**.

Tulemus salvestub History-sse (tipp-tok/s), nii et näed jooksude-vahelist muutust. Nuppudega
**"Copy sweep table"** ja **"Copy report"** saab lõikelauale kopeerida vastavalt paralleelsuse
sweep-tabeli (tab-eraldatud) või kogu testi transkriptsiooni (compliance + integrity + verdiktid) —
mugav tulemuse jagamiseks.

> **🧠 "Lülita thinking testi ajaks välja" (vaikimisi sees):** saadab iga päringuga
> `chat_template_kwargs.enable_thinking=false`, testides Qwen3-stiilis reasoning-mudelit tema agentses
> režiimis. Thinking-režiimis kipub selline mudel "ülemõtlema" ja vastama proosas, kutsumata tööriista
> — nii kukuksid tool-proovid, kuigi mudel on võimekas. Võta ruut maha, et testida thinking-varianti
> nii-nagu-on. Serverid, mis parameetrit ei toeta, ignoreerivad seda.

> **Märkus allika kohta:** compliance-kontrollid on vastavuses OpenRouteri
> ([provider integration](https://openrouter.ai/docs/guides/community/for-providers)) ja HF
> ([register-as-a-provider](https://huggingface.co/docs/inference-providers/en/register-as-a-provider))
> dokumentatsiooniga. **Pudelikaela-taksonoomia (prefill/decode/queue/batching) on aga selle tööriista
> oma analüütiline raamistik** — põhjendatud sellega, kuidas vLLM/TGI päriselt töötavad, mitte otsene
> nõue routerite dokumentidest (OpenRouter avaldab avalikult ainult TTFT-d ja throughput'i).

> **Mida inference-API kaudu EI saa kontrollida (ausalt):** *no-charge-on-error* (kas ebaõnnestunud
> päringut ei arveldata) nõuab routeri billing-API-t, mitte ainult inference-otspunkti; ja **päris
> kvantimise-fingerprint** nõuaks referents-logprobe iga mudeli kohta (meil on ainult enesekindluse
> proxy). Need jäävad teadlikult katmata.

## Capabilities (mida endpoint pakub)

**Capabilities**-tab **kaardistab, mis funktsionaalsust see server/mudel tegelikult pakub** — kasulik,
kui on vaja kiiresti teada, kas endpoint toetab nt **embedding'uid**, rerank'i või vision'it, ilma
dokumentatsiooni kaevamata. Iga rida on **üks väike proov** praeguse Host / Model vastu, tulemusega
✓ *supported* / ✗ *no* / ~ *present* (marsruut olemas, aga sel mudelil ei tööta) / — *n/a*.

Kolm rühma:

- **API-marsruudid** — kas server teenindab neid otspunkte:
  - `/v1/models` (mudelite loend), `/v1/chat/completions`, `/v1/completions`
  - **`/v1/embeddings`** — kui töötab, näidatakse **vektori dimensioon**. Kui valitud (chat)mudel
    ei embed'i, **proovitakse automaatselt teisi `/v1/models` all olevaid mudeleid** (embedding-nimelised
    esimesena) — nii näed ühe skanniga, kas router üldse embeddings'e pakub ja **millise mudeliga**.
  - `/v1/rerank` (või `/rerank`), `/tokenize` (vLLM), `/v1/moderations`
  - `/v1/images/generations`, `/v1/audio/speech` (TTS), `/v1/audio/transcriptions` (STT)
  - *Marsruudi-tuvastus:* iga vastus peale 404 (ka 400/422 valideerimisviga) tähendab, et otspunkt on
    olemas — nii eristub "otspunkti pole" tegelikust "otspunkt on, aga see mudel ei sobi".
- **Chat-funktsioonid** (kui chat-otspunkt töötab):
  - **Voogedastus (SSE)**, **natiivne tööriista-kutse**, **JSON object mode** ja **JSON schema mode**
    (structured outputs), **vision** (pildi-sisend), **mitu vastust (n>1)**, **logprobs**,
    **stop-jadad**, **korratav sämplimine (seed)**, **reasoning/thinking** väljund.
- **Mudeli metaandmed** — `/v1/models` kirjest: konteksti pikkus, hinnakiri, omanik, mudelite arv.

Skann teeb ~kaks tosinat kiiret päringut ja **ei tekita koormust**. Tulemuse saab **Copy results**
nupuga tab-eraldatud tabelina lõikelauale.

## Embed speed (embeddingu kiirus)

**Embed speed**-tab mõõdab **embedding-mudeli jõudlust ja kiirust** — mitu vektorit sekundis server
suudab toota ja kui kiiresti üks päring vastab. Erineb teistest testidest, mis mõõdavad chat-mudeli
genereerimist: siin läheb koormus `/v1/embeddings` otspunkti.

Kuidas töötab:

- **Batch-koormus:** `concurrency` töölist saadavad pidevalt päringuid, igas päringus **`batch_size`
  teksti** (~`input_tokens` tokenit tekst) `duration_s` sekundi jooksul. Embedding-serverid **batch'ivad
  väga efektiivselt**, seega on batch-suurus eraldi nupp — suurem batch tähendab tavaliselt palju rohkem
  embeddings/s (kuni serveri piirini).
- **Preflight:** enne testi tehakse üks väike embed, et kinnitada, et mudel **päriselt embed'ib** ja
  saada vektori dimensioon. Kui valid chat-mudeli, mis ei embed'i, peatub test **selge teatega**
  (nt *"model X is not an embedding model"*).
- **Mudeli valik:** embedding-mudel on **tavaliselt erinev** su chat-mudelist (nt bge-m3, e5, nomic).
  Sisesta see "Embedding model" väljale (tühjaks jättes kasutab ülal valitud mudelit). Jooksuta enne
  **Capabilities** tab, et näha, milline mudel embed'ib.

**Tulemus:** **embeddings/s** (vektorit sekundis), **sisend-tokenit/s**, req/s, **vektori dimensioon**,
latents **p50/p95** ja **ms ühe embeddingu kohta**. Reaalajas graafik näitab embeddings/s ajas.

**Näide:** bge-m3, batch 32, concurrency 8, 64 tok/text, 15 s → näed nt "2 500 emb/s · 160 K tok/s
(dim 1024) · p95 24 ms". Tõsta batch-suurust (64 / 128), et leida tipp-läbilaskevõime.

> ⚠️ Nagu teised koormustestid, **koormab Embed speed serverit** — jooksuta ainult serverite vastu,
> mida sa omad või milleks sul on luba.

## Embed quality (kas embeddingud töötavad)

**Embed quality**-tab vastab küsimusele, mida kiirus ei ütle: **kas need embeddingud on tegelikult
head?** Kiire mudel on kasutu, kui vektorid on mõttetud. Iga rida on üks väike proov → ✓ *pass* /
✗ *fail* / ~ *weak* koos mõõdetud numbritega. Neli rühma:

- **Retrieval & sarnasus** — kas embeddingud kannavad tähendust:
  - **Retrieval-järjestus:** päring + dokumendid → **õige dokument peab saama kõrgeima cosine-sim'i**.
  - **Parafraas vs mitteseotud:** parafraaside sim peab olema **selgelt kõrgem** kui mitteseotud tekstidel.
  - **Mitmekeelsus (eesti↔inglise):** eesti lause peab embed'uma **lähemale oma inglise tõlkele** kui
    mitteseotud inglise lausele — testib cross-lingual retrieval'i (relevantne eestikeelse sisu jaoks).
- **Vektori omadused** — kas sobib vektor-DB-sse:
  - **L2-normaliseeritus** (‖v‖ ≈ 1 — paljud vektor-DB-d eeldavad seda), **determinism** (sama tekst →
    identne vektor), **dimensioon**.
- **Piirid** — kliendi batch'imise disainiks:
  - **Max sisend-pikkus** (kus mudel kärbib/vea annab) ja **max batch-suurus** (mitu teksti/päring).
- **Rerank** (kui `/v1/rerank` olemas) — **relevantsus:** kas reranker paneb relevantse dokumendi
  esimeseks. Kui embedding-mudel pole reranker, otsitakse automaatselt rerank-nimelist mudelit.

Skann teeb ~20 kiiret päringut ja **ei tekita koormust**. Embedding-mudel on tavaliselt chat-mudelist
erinev — jooksuta enne **Capabilities** tab, et näha, milline mudel embed'ib. **Copy results** kopeerib
tab-eraldatud tabeli.

## Eelseaded, võrdlus ja raportid

### Koormuse eelseaded

**Connection**-tabil on kolm nuppu, mis täidavad ühe klõpsuga mõistlikud parameetrid **korraga**
Benchmark-, Soak- ja Provider-fit-tabidel — nii ei pea iga välja käsitsi sättima:

| Eelseade | Kirjeldus | Näide (in / out / concurrency) |
|---|---|---|
| **Vestlus** | Lühikesed promptid, lühikesed vastused, mõõdukas paralleelsus (interaktiivne chat) | ~1k / 256 / 8–32 |
| **RAG (pikk kontekst)** | Suur sisend, mõõdukas väljund | ~8k / 256–500 / 8–16 |
| **Agent / batch** | Kõrge paralleelsus, lühike struktuurne väljund | ~2k / 384 / 32–64 |

Väärtused on lähtepunkt — muuda neid pärast vajadusel käsitsi.

### Risthost/-mudel võrdlus

**History**-tabil vali **mitu rida** (Cmd/Shift-klõps) ja vajuta **"Võrdle valitud"** — avaneb
kõrvutine tabel: **read = mõõdikud, veerud = jooksud** (märgistatud `host:port · mudel · test`).
Erinevalt tavalisest jooksude-vahelisest võrdlusest (mis grupeerub sama konfigi järgi) töötab see
**üle erinevate serverite, mudelite ja konfiguratsioonide** — st päris "server A vs server B" või
"mudel X vs mudel Y".

### Jagatav raport

**"Ekspordi raport"** salvestab valitud jooksu(d) **Markdown**- või **HTML**-failina (vali laiendi
järgi). Üksik jooks annab metaandmed + mõõdikute tabeli; mitu valikut annab võrdlustabeli. Sama nupp
on ka võrdlus-aknas. Kõik tulemused on lisaks eksporditavad **CSV**-na ("Export CSV…").

### Mugavus

- **Valmimise-teavitused** — kui pikk test (≥ 8 s) lõpeb, tuleb macOS desktop-notification + heli
  (nii saad testi ajal eemale minna).
- **Kiirklahvid** — `Cmd+R` käivitab aktiivse tabi testi, `Cmd+.` / `Esc` peatab, `Cmd+D` tuvastab
  serveri, `Cmd+L` loetleb mudelid.

## Kuidas see töötab

- **Kiiruse mõõtmine** kasutab voogedastust (`stream=True`): aeg esimese tokenini (TTFT)
  mõõdetakse esimese sisuga chunki saabumisel, dekodeerimise kiirus = tokenid / (lõpp − esimene token).
  Kui server toetab `stream_options.include_usage`, kasutatakse täpseid tokenite arve (vLLM, SGLang).
- **Tuvastus** proovib mitut tunnusjälge: `/v1/models`, `/api/tags` (Ollama), `/props` (llama.cpp),
  `/get_model_info` (SGLang), `/info` (TGI), `/version` (vLLM) + pordi-heuristika. "Detect server"
  proovib enne veel skeemi-kandidaadid (https/http) läbi, kuni üks vastab.
- **Optimum finder** ehitab koormustesti (`load`) peale: faas A binaarotsib max konteksti (või loeb
  serveri teatatud limiidi), faas B ronib paralleelsust varajase peatamisega ja arvutab peak/knee,
  faas C otsib iga kontekstisuuruse juures suurima toimiva paralleelsuse. Iga päring saab unikaalse
  filler-prompti, et prefix-caching numbreid ei moonutaks.
- **Skann** teeb asünkroonse TCP-connect skanni alamvõrgule valitud portidel ja seejärel
  tuvastab leitud avatud portide taga olevad serverid.

## Eetiline märkus

Skanni ainult võrke, mida sa **omad või millel on luba testida**. Võõraste võrkude
skannimine võib olla seadusevastane.

## Projekti struktuur

```
llmscanner/
├── gui.py        # Tkinter GUI (peamine) — Connection / Benchmark / Optimum finder / Soak / Capacity / Model fit / Provider fit / Capabilities / Embed speed / Embed quality / Scan / History
├── cli.py        # käsurea-liides (boonus, vajab rich)
├── client.py     # OpenAI-ühilduv async klient (http/https, base_path) + ajamõõtmine
├── detect.py     # serveri tuvastus / fingerprint + smart_detect (kandidaatide proovimine)
├── scanner.py    # võrguskann + portide tuvastus
├── benchmark.py  # latentsus / koormus / kontekst / sanity / sweep + find_optima + soak_test + capacity_test + suitability_test (model fit) + provider_readiness + capabilities_probe + embed_speed_test + embed_quality_test
├── store.py      # SQLite püsivus: salvestatud hostid + kõik tulemused
├── icon.py       # rakenduse ikooni (sinine V) genereerimine
├── assets/
│   └── icon.png  # genereeritud aknaikoon
├── models.py     # andmeklassid (ServerInfo, RequestResult)
└── util.py       # abifunktsioonid + Target / resolve_target (nutika host-välja parsimine)

app_entry.py      # PyInstaller entry point (käivitab GUI)
build_macos.sh    # ehitab iseseisva ühe-faililise macOS executable'i (dist/LLMScanner)
run.sh            # käivitab rakenduse (loob venv + install esimesel korral)
run.command       # topeltklõpsatav macOS Finderi käivitaja
CHANGELOG.md      # muudatuste logi
```

GUI sisemus: korduvkasutatavad komponendid `ChartCanvas` (kerge joongraafik) ja `LiveLog`
(voogedastav värviline logi) jagatakse tabide vahel; tabelid on `ttk.Treeview`, mis on stiilitud
CustomTkinteri välimuse järgi (sebra, ümberjärjestatavad tulbad).

### Abi, teema ja keel

Akna **üleval paremal** on seaderiba:

- **❔ Abi / Help** — avab juhendi (vahekaartide ülevaade + näpunäited) ning infrastruktuuri
  (**Visioline Infra**) ja toe (**support@itteam.eu**) kontaktid.
- **Teema** — Süsteem / Hele / Tume; rakendub kohe.
- **Keel** — inglise (primary) / eesti.

Teema ja keele valik **jäetakse meelde** (salvestub `~/.llmscanner`-i) ja taastub järgmisel
käivitusel. Keele vahetus ehitab vahekaardid uuesti — seda ei tehta jooksva testi ajal.

### Andmete asukoht

Salvestatud hostid, kõik testitulemused ja seaded (teema, keel) hoitakse failis
`~/.llmscanner/llmscanner.db` (SQLite). Asukohta saab muuta keskkonnamuutujaga `LLMSCANNER_HOME`.

Muudatuste ajalugu on failis [CHANGELOG.md](CHANGELOG.md).
