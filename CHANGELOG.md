# Muudatuste logi

Kõik märkimisväärsed muudatused selles projektis. Vorming järgib
[Keep a Changelog](https://keepachangelog.com/) põhimõtteid.
Praegune versioon: **0.1.0** (väljalaskeid pole veel märgistatud; allpool kuupäeva järgi).

## [Märgistamata]

### 2026-07-07 (Capabilities: parandatud timeout-viga + /health proov)
- **Parandatud `TypeError: httpx.AsyncClient() got multiple values for keyword argument 'timeout'`**,
  mis kukutas KÕIK Capabilities-tabi endpoint-proovid (chat, completions, embeddings jne näitasid
  valelt "no" koos TypeError-detailiga). Põhjus: `probe_json` andis `_http(timeout=…)`, aga `_http`
  seadis `timeout` juba ise — nüüd kasutab `_http` `setdefault`-i, nii et kutsuja saab üle kirjutada.
- **Lisatud `/health` marsruudi proov** (enamik routereid/vLLM pakub seda). Nüüd testitud päris
  `LLMClient` + httpx MockTransport'iga (mitte ainult mock-objektiga), mis oleks selle vea kohe tabanud.

### 2026-07-07 (uus Capabilities-tab — funktsionaalsuse avastus)
- **Uus "Capabilities" tab (Provider-fit järel)** — kaardistab, mis funktsionaalsust endpoint/mudel
  pakub. Kolm rühma: **API-marsruudid** (`/v1/models`, chat, completions, **`/v1/embeddings`** koos
  vektori dimensiooniga, `/v1/rerank`, `/tokenize`, `/v1/moderations`, images, audio speech/transcribe),
  **chat-funktsioonid** (voogedastus, natiivne tööriista-kutse, JSON object/schema režiim, vision,
  n>1, logprobs, stop-jadad, seed-korratavus, reasoning) ja **mudeli metaandmed** (konteksti pikkus,
  hinnakiri, omanik). Iga rida = üks väike proov → ✓ supported / ✗ no / ~ present / — n/a.
- Marsruudi-tuvastus: iga vastus peale 404 loeb marsruudi olemasolevaks, nii eristub "otspunkti pole"
  tegelikust "otspunkt on, aga see mudel ei toeta" (nt embeddings üldmudelil).
- Tulemused värvikoodiga puus (rühma-päised + ridade rohelin/punane/oranž), **Copy results** kopeerib
  tab-eraldatud tabeli. Skann ei tekita koormust (~2 tosinat kiiret päringut).
- Klient sai `probe_json(method, path, body)` madalataseme otspunkti-proovija; backend `capabilities_probe()`.

### 2026-07-06 (Provider-fit tabi paigutus korda)
- **Provider-fit väljade paigutus parandatud** — parem väljapaar (Output tokens / Requests per level /
  Context probe) oli varem paisatud kaugele paremasse serva (osaliselt ekraanilt välja). Põhjus:
  murdumatu intro-tekst paisutas grid'i ~1650px laiuseks ja ilma venitava veeruta hajusid väljad
  laiali. Nüüd: intro **murdub** pane laiusele (ja resize'imisel dünaamiliselt), väljad on **kahes
  joondatud veerus** (labelid paremjoondatud, ühtlane vahe) ja **venitav sabaveerg** hoiab väljad
  vasakul kompaktselt. Neli kontroll-linnukest on koondatud "Kontrollid" alapealkirja alla.
- **Uus "Capacity" tab (Soak ja Model-fit vahel)** — mõõdab endpoint'i **tipp-püsiva tokenit/minutis**
  ehk võimsuse **lae**. Erinevalt Soak-ist (fikseeritud concurrency → tok/tunnis) **tõstab Capacity
  concurrency't astmeliselt (1 → 2 → 4 → … → Max concurrency)**, hoiab igal astmel koormust
  "Window / step" sekundit (vaikimisi 40 s), viskab akna esimese ~kolmandiku ära (warm-up) ja mõõdab
  ülejäänu pealt steady-state IN/OUT/TOTAL tok/min.
- **Auto-saturatsioon:** ramp peatub varakult, kui läbilaskevõime platoole jõuab (< 8% kasvu),
  server hakkab tagasi lükkama (429/503), tekivad kõvad vead/timeout'id või väljund kärbitakse.
  Tulemus näitab **tipp-TOTAL tok/min, millisel concurrency'l** ja **miks ramp peatus** (+ tok/h
  projektsioon ja küllastuskõvera graafik). Kui jõuab max-ni ilma peatumata → *"still climbing"*.
- **Valikuline "Target tok/min" väli → PASS/FAIL** verdikt: kas mõõdetud tipp-võimsus täidab nõutava
  tokenit/minutis (nt lepingu TPM). Tühjaks jättes lihtsalt mõõdab lae.
- Backend: `capacity_test()` + `_capacity_levels()` (benchmark.py); GUI: `_build_capacity_tab` +
  handlerid, eestikeelsed tõlked, Cmd+R tugi. Nagu Optimum finder / Soak, on ka Capacity koormustest.

### 2026-07-06 (Capacity-tabi visuaalne lihv + täpsem diagnostika)
- **Graafik loetavam:** teljesildid nüüd kompaktsed (31.2M, mitte 31242857) — parandus kehtib
  kõigile graafikutele (Soak, Benchmark jt). Capacity-kõveral on **saturatsioonipunktid punased**
  ja **tipp rõngastatud** rohelise "peak"-markeriga.
- **Verdikt värviline:** suur readout läheb tulemusega roheliseks (võimsus leitud / target täidetud)
  või punaseks (target täitmata / püsivat võimsust pole); target-tulemus (✅/❌) on nüüd ka readout'is.
  Logi sammuread joondatud veergudesse, ebatervete tasemete juures ⚠.
- **Täpsem diagnostika:** kui ükski päring ei jõua mõõteakna sees valmis (aken lühem kui päringu
  kestus), öeldakse nüüd selgelt *"no request finished inside the measurement window (a request
  takes ~Xs vs Ys window) — raise ‘Window / step’"* varasema eksitava "output truncated" asemel.
- Backend emitib `step_done` hetkeseisu nüüd värske peak'iga (varem jäi readout sammu võrra maha).

### 2026-07-06 (Hermes tööriista-proov nüüd tagavara, mitte alati)
- **Provider-fit'i "Tool calling (Hermes prompt)" kontroll on nüüd TAGAVARA** — see jookseb ainult siis,
  kui natiivne `tools` API-kontroll (mis gate'ib verdikti) ei tööta. Natiivse tööriista-kutsega mudel
  (nt Qwen3) ei vaja prompt-põhist Hermes/NousResearch `<tool_call>` XML-konventsiooni, seega näidatakse
  nüüd rohelist _"n/a — native tool-calling works"_ segadust tekitava punase _"0/3 correct Hermes tool
  calls"_ asemel. Kui natiivne kukub, testitakse Hermes-t nagu varem (3 juhtu, näidatakse mudeli
  tegelikku vastust). Kumbki kontroll ei mõjuta verdikti — mõlemad on informatiivsed.

### 2026-07-06 (Model-fit "Copy results" nupp)
- **Model-fit tabil "Copy results" nupp** — kopeerib lõikelauale raporti (verdikt + skoorid) ja
  kogu proovi-tabeli **täis-detailidega** (sh täielikud vea-teated), tab-eraldatud kujul. Sama mustri
  järgi nagu Provider-fit / Benchmark / Optimum finder.

### 2026-07-06 (mööduva serveri-tõrke (5xx) automaatne kordamine proovidel)
- **Võimekus-proovid (compliance / integrity / model-fit / recall) kordavad nüüd mööduva 5xx serveri-
  tõrke (nt hetkeline 503 üle-koormus) või ühendus-/timeout-vea korral automaatselt** (kuni 2× väikese
  backoff'iga). Varem võis serveri hetkeline hikk kukutada kogu testi (nt kõik tool-proovid → "HTTP 503"
  → vale "EI SOBI"). **Koormus-/soak-tee EI korda** — seal on 503 just admission-control signaal, mida
  mõõdame. Klient sai `generate(..., retries=N)` parameetri ja `_is_transient()` eristuse (5xx/ühendus-
  viga korratav, 4xx mitte).
- **Ebaõnnestunud tool-proovi detail hoiab nüüd KOGU serveri veateate** (varem kärbitud 60 tähega), nii
  et Model-fit real topeltklõps näitab täielikku 503-vastust — vajalik server-poolse tõrke (nt katkine
  tool-genereerimise tee) diagnoosimiseks. Klient püüab vea-keha nüüd 600 tähe ulatuses.

### 2026-07-06 (calculator-juhtumid eksplitsiitseks; klikk-avab-detaili)
- **Model-fit tabelil topeltklõps real avab mudeli täis-prompti ja täis-detaili** eraldi aknas
  (tabeliveerud lõikavad pika teksti ära, nt "→ no tool call — model said: …").
- **Model-fit calculator-juhtumid teevad nüüd eksplitsiitse tööriista-palve** ("Use the calculator
  tool to compute …"). Varem: võimekas mudel arvutas lihtsa aritmeetika ise (õigesti!) tööriista
  kutsumata, mida test luges veaks — see kõigutas tool-skoori juhuslikult (nt 76% ↔ 88%). Nüüd on
  ootus üheselt tööriista-kutse, seega juht on deterministlik. (Weather/search/email juhud on
  muutmata — need vajavad päriselt tööriista.)

### 2026-07-06 (Model-fit natiivne tool-calling; native-värav ka OpenRouterile; completions-serv)
- **Model-fit testib nüüd natiivset tool-callingut** (OpenAI `tools` API), Hermes-prompt tagavarana.
  Varem testis Model-fit **ainult** Hermes-`<tool_call>` konventsiooni, mistõttu natiivset tool-callingut
  toetav (aga Hermes-XML-i mitte-emiteeriv) mudel sai valelt "EI SOBI (Hermes)". Nüüd krediteeritakse
  mudelit, kui ta kutsub tööriista **kumbat tahes** viisi; ainult mudel, kes kumbagi ei tee, saab nulli.
  Verdiktist eemaldatud "(Hermes)" spetsiifika.
- **Model-fit sai "Lülita thinking testi ajaks välja" märkeruudu** (vaikimisi sees) — sama nagu
  Provider-fit, et Qwen3-stiilis reasoning-mudelit testitakse agentses režiimis.
- **Natiivne tool-calling gate'ib nüüd ka OpenRouteri verdiktit** (lisaks HuggingFace'ile) — router,
  mis suunab tool-calling liiklust, vajab, et mudel `tools` API-t toetaks.
- **Parandus:** Provider-fit natiivne tool-test näitab `/v1/completions` otspunktil ausalt
  "n/a — completions-il pole tools API-t", mitte eksitavat "no tool_calls — model said: …"
  (legacy completions-endpointile ei saagi `tools` parameetrit saata).

### 2026-07-05 (thinking-välja-lülitamise valik Provider-fit'is)
- **Uus märkeruut "Lülita thinking testi ajaks välja (testi agentset režiimi)"** — vaikimisi **sees**.
  Saadab iga testi-päringuga `chat_template_kwargs.enable_thinking=false`, nii et Qwen3-stiilis
  reasoning-mudelit testitakse tema **agentses (thinking-off) režiimis**. Põhjus: Provider-fit mõõdab,
  kas backend suudab teenindada **agentset / tool-calling liiklust**, ja thinking-režiimis kipub selline
  mudel "ülemõtlema" — arutleb proosas ja vastab otse, kutsumata tööriista, mistõttu tool-proovid
  kukuvad kuigi mudel on võimekas. Ruudu saab maha võtta, et testida thinking-varianti nii-nagu-on.
  Serverid, mis parameetrit ei toeta, lihtsalt ignoreerivad seda.
- Klient (`client.py`) sai üldise `extra_body` läbiviigu — suvalised top-level request-body väljad
  ühendatakse igasse päringusse (ilma parameetrit igale kutsele käsitsi läbi andmata).

### 2026-07-05 (natiivne tool-calling test + diagnostika toorvastusega)
- **Uus kontroll: "Tool calling (native API)"** — Provider-fit saadab nüüd päris OpenAI `tools`/
  `tool_choice` API-parameetri (mitte ainult prompt-põhist Hermes-konventsiooni) ja loeb vastuse
  `tool_calls` välja (nii streaming `delta.tool_calls` fragmentide kokkupanek kui non-streaming
  `message.tool_calls`). See on tänapäeval **päris standard**, mida OpenRouter/vLLM/TGI/SGLang
  kasutavad — vana Hermes-XML test testis vaid **ühte kindlat fine-tune'i konventsiooni**, mistõttu
  hea, natiivset tool-callingut toetav mudel sai varem valelt "EI SOBI".
  - Vana kontroll on ümber nimetatud **"Tool calling (Hermes prompt)"** ja jääb infoks (ei mõjuta enam
    verdikti), samal ajal kui **"Tool calling (native API)" gate'ib nüüd HuggingFace'i verdikti**.
  - Klient (`client.py`) sai `tools`/`tool_choice` läbiviigu ja `RequestResult.tool_calls` välja;
    TTFT arvestab nüüd ka tool-call-only vastuseid (muidu näinuks voog "mitte-voogedastatuna").
- **Ebaõnnestunud tool-call proovid näitavad nüüd toorvastust.** Varem kuvati lihtsalt "→ ∅" kui
  midagi ei õnnestunud parsida — ei saanud vahet teha, kas mudel ignoreeris tööriistu täielikult
  või proovis teises vormingus. Nüüd (nii Provider-fit'is kui Model-fit'is) näidatakse mudeli
  tegelikku vastust (lühendatult), nii et jooks on ise-diagnoosiv.

### 2026-07-05 (aken sobitub ekraaniga; Provider fit copy-nupud)
- **Aken sobitub erineva suurusega ekraanidele** — akna algsuurus (ja Abi- ning Võrdlus-akende
  suurus) arvutatakse nüüd ekraani mõõtude järgi (kuni 92%/88% laius/kõrgus, tsentreeritud), mitte
  fikseeritud konstandi järgi. Varem võis 1400×1010 aken avaneda **suuremana kui väiksem kuvar**
  (nt väiksem sülearvuti ekraan või tiled/split-screen paigutus). Minimaalne akna suurus on piiratud
  **arvutatud algsuurusega, mitte ekraaniga eraldi** — ülevaatusel selgus, et kaks eri valemit oleks
  väiksel ekraanil võinud `minsize`-i suuremaks arvutada kui algsuurus, mille peale Tk sunniks akna
  kohe suuremaks (tühistades ekraanile-sobitamise); nüüd on `minsize ≤ algsuurus` alati tagatud.
- **Provider fit — "Copy sweep table" ja "Copy report" nupud.** Esimene kopeerib paralleelsuse
  sweep-tabeli (tab-eraldatud, nagu Benchmark/Optimum finder). Teine kopeerib **kogu testi
  transkriptsiooni** (compliance + integrity + verdiktid) lõikelauale tavatekstina — täpselt
  see, mida vajad tulemuse jagamiseks kolmanda osapoolega (nt inseneriga, kes hindab backend'i).

### 2026-07-05 (reasoning-mudelite tugi Provider-fit'is)
- **Reasoning-mudelid** (nt DeepSeek-R1 / Qwen thinking) ei anna enam valet "EI SOBI" raportit.
  Varem: kui mudel pani nähtava vastuse `reasoning_content`-i ja väike token-eelarve kulus peidetud
  arutlusele, sai tööriist teksti tagasi tühjana → kaskaad valesid kukkumisi (sh vale "token-
  inflatsioon ×N" ja "kvaliteet 0%").
  - Klient püüab nüüd **`reasoning_content` / `reasoning`** (streaming + non-stream), loeb need
    chunkide sisse ja **mõõdab TTFT ka esimese reasoning-tokeni pealt** (parandab streaming-tuvastuse).
  - **Token-aususe test loeb reasoning-tokenid kaasa** — thinking-mudelit ei süüdistata enam
    billing-inflatsioonis (reaalsed tokenid on ausad, ka kui `content` on tühi).
  - Provider-fit **tuvastab reasoning-mudeli** ja annab korrektsus-/kvaliteedi-/recall-proovidele
    **laiendatud token-eelarve** + eemaldab `<think>…</think>`, et jõuda nähtava vastuseni.
  - Raport märgib "🧠 reasoning model" ja History salvestab `reasoning model: yes/no`.
  - Tuvastus ja eemaldus töötavad ka **kohaliku serveri stiiliga** reasoning-mudelitel (vLLM /
    llama.cpp / SGLang), kus `<think>` on **otse `content`-is**, mitte eraldi `reasoning_content`
    väljas — sh juhul kui token-eelarve ei jõua sulgevat `</think>` silti kätte saada (poolelijäänud
    arutlus loetakse tervikuna reasoning'uks, mitte vastuseks).

### 2026-07-05 (mugavus & andmed)
- **Koormuse eelseaded** — Connection-tabil nupud **Vestlus / RAG / Agent**, mis täidavad ühe
  klõpsuga mõistlikud parameetrid Benchmark-, Soak- ja Provider-fit-tabidel.
- **Risthost/-mudel võrdlus** — History-tabil vali mitu rida (Cmd/Shift-klõps) ja **"Võrdle valitud"**
  avab kõrvutise tabeli (mõõdikud × jooksud) — server A vs B, mudel X vs Y, ka üle erinevate konfigide.
- **Jagatav raport** — **"Ekspordi raport"** salvestab valitud jooksu(d) Markdown- või HTML-failina
  (metaandmed + mõõdikute tabel; mitme valiku puhul võrdlustabel).
- **Valmimise-teavitused** — macOS desktop-notification + heli, kui **pikk** test (≥8 s) lõpeb.
- **Kiirklahvid** — Cmd+R (käivita aktiivse tabi test), Cmd+. / Esc (peata), Cmd+D (detect),
  Cmd+L (loetle mudelid).
- Parandus: Provider-fit nupp keelatakse nüüd samuti jooksva testi ajal; staatusriba "Valmis." järgib
  keelevalikut.

### 2026-07-05 (UI-seaded)
- **Abi-aken** (üleval paremal ❔) — juhend vahekaartide kaupa + näpunäited, **Visioline Infra**
  infrastruktuuri rida ja **support@itteam.eu** kontakt (nupp kopeerib aadressi). Sisu on kakskeelne.
- **Teema-valik** — Süsteem / Hele / Tume, rakendub kohe. CustomTkinteri widget'id uuenevad ise;
  `_retheme()` värskendab paleti custom tk-graafikutel, `LiveLog`-idel ja ttk-tabelitel. Taastub
  käivitusel.
- **Keelevalik** — inglise (primary) / eesti. Kerge `L()` abifunktsioon (inglise string = võti,
  tõlkimata → inglise fallback), rakendatud tsentraalselt `_section`-is ja `_lbl`-is; tõlgitud on
  vahekaartide nimed, sektsioonid, väljasildid, nupud, checkbox'id. Keele vahetus ehitab vahekaardid
  uuesti (blokitud jooksva testi ajal; ajalugu ja ühendusväljad säilivad). ⓘ-abitekstid ja pikad
  kirjeldused jäävad inglise keelde.
- **Seadete püsivus** — uus `store` settings-tabel (`get_setting`/`set_setting`); teema ja keel
  salvestuvad `~/.llmscanner`-i.

### 2026-07-04
- **Provider fit** — uus vahekaart, mis hindab, kas backend kannatab OpenRouter/HuggingFace liiklust
  ja kus ta esimesena katki läheb:
  - **API-lepingu vastavus** (14 kontrolli) — streaming, usage-arvestus, max_tokens, stop, determinism,
    sampling-parameetrid, concurrent-korrektsus, puhtad veakoodid, tool-calling, structured output,
    `/v1/models` metadata (pricing + context_length), API-võtme auth-jõustamine.
  - **Aususe-testid** — token-loenduse ausus (kõva OpenRouter-blokk billing-inflatsiooni vastu),
    konteksti-ausus (needle-in-haystack), mudeli kvaliteet (golden-answer eval), kliendi-katkestuse
    käitlus, logprob-fingerprint.
  - **Paralleelsuse sweep** — läbilaskevõime põlv + pudelikaela-klassifikatsioon (prefill/decode/
    batching/admission/stabiilsus), p95 **ja p99** latents.
  - **Verdikt** kummalegi pakkujale (SOBIB / PIIRIPEAL / EI SOBI), vastavuses nende dokumentatsiooniga
    (nt HF 5 s TTFT lävi). Tulemus salvestub History-sse.
  - Klient sai `finish_reason`, `stop`/`top_p`/`seed`/`logprobs` läbiviigu, `stream_chunks`,
    `logprob_avg`, `stream_abort()` ja `list_models_raw()` — kõik tagasiühilduvad.
- **Model fit** ühendatud History/võrdlusega (üldsobivus %, jooksude-vaheline muutus).
- **Iseseisev macOS executable** — `build_macos.sh` + `app_entry.py` → `dist/LLMScanner`
  (PyInstaller `--onefile`, ikoon genereeritakse jooksul).
- **`run.sh` / `run.command`** — ühe käsuga käivitus (loob venv + install esimesel korral).

### 2026-07-03
- **Soak-test** — uus vahekaart: hoiab püsivat koormust N minutit ja mõõdab **tokeneid tunnis**;
  toetab **TheEye** päris-koormuse kordamist ja **ülekoormuse-proovikut** (+10%, admission-control).
- Hinnanguliste tokeni-loendite märgistamine, kui server ei saada usage-blokki.
- Under-generation'i märgistamine, kui server ignoreerib `ignore_eos`.
- Vaikeväärtused: concurrency lagi 64, requests-per-worker 2, max-konteksti lagi 65536, settle-paus 3 s.

### 2026-07-02
- **Esmane väljalase** — LLM Scanner: lokaalsete LLM-serverite (vLLM, SGLang, Ollama, llama.cpp,
  TGI, LM Studio) avastamine, benchmarkimine ja häälestamine Mac-sõbraliku GUI-ga
  (Connection / Benchmark / Optimum finder / Network scan / History).
- **Cancel-nupp** Optimum finderi testi katkestamiseks.
