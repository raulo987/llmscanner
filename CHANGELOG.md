# Muudatuste logi

Kõik märkimisväärsed muudatused selles projektis. Vorming järgib
[Keep a Changelog](https://keepachangelog.com/) põhimõtteid.
Praegune versioon: **0.1.0** (väljalaskeid pole veel märgistatud; allpool kuupäeva järgi).

## [Märgistamata]

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
