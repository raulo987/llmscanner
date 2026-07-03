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
  tunnis** (+ kas läbilaskevõime püsib stabiilne). Vt [Soak-test](#soak-test).
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
```

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
├── gui.py        # Tkinter GUI (peamine) — Connection / Benchmark / Optimum finder / Scan / History
├── cli.py        # käsurea-liides (boonus, vajab rich)
├── client.py     # OpenAI-ühilduv async klient (http/https, base_path) + ajamõõtmine
├── detect.py     # serveri tuvastus / fingerprint + smart_detect (kandidaatide proovimine)
├── scanner.py    # võrguskann + portide tuvastus
├── benchmark.py  # latentsus / koormus / kontekst / sanity / sweep + find_optima (optimum finder)
├── store.py      # SQLite püsivus: salvestatud hostid + kõik tulemused
├── icon.py       # rakenduse ikooni (sinine V) genereerimine
├── assets/
│   └── icon.png  # genereeritud aknaikoon
├── models.py     # andmeklassid (ServerInfo, RequestResult)
└── util.py       # abifunktsioonid + Target / resolve_target (nutika host-välja parsimine)
```

GUI sisemus: korduvkasutatavad komponendid `ChartCanvas` (kerge joongraafik) ja `LiveLog`
(voogedastav värviline logi) jagatakse tabide vahel; tabelid on `ttk.Treeview`, mis on stiilitud
CustomTkinteri välimuse järgi (sebra, ümberjärjestatavad tulbad).

### Andmete asukoht

Salvestatud hostid ja kõik testitulemused hoitakse failis `~/.llmscanner/llmscanner.db`
(SQLite). Asukohta saab muuta keskkonnamuutujaga `LLMSCANNER_HOME`.
