#!/usr/bin/env python3
"""
Genera il calendario iCal (.ics) di tutte le partite del Napoli Basket:
Serie A (regular season, playoff), Coppa Italia, Supercoppa e EuroCup.

Fonti ufficiali e gratuite:
  - Lega Basket Serie A:  https://www.legabasket.it/api/...
  - EuroCup (Euroleague):  https://api-live.euroleague.net/v2/...

Nessuna libreria esterna: basta Python 3.9+.
Se una fonte non risponde, vengono riusate le partite salvate all'ultima
esecuzione riuscita (data/cache.json), così il calendario non si svuota mai.
"""

import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------- impostazioni
SQUADRA = "Napoli"                 # parola cercata nel nome della squadra
CODICE_EUROCUP = "NAP"             # codice club in EuroCup
NOME_CALENDARIO = "🏀 Napoli Basket – tutte le partite"
DURATA_PARTITA = timedelta(hours=2)
EMOJI = "🏀"

ROOT = Path(__file__).resolve().parent
FILE_ICS = ROOT / "docs" / "napoli-basket.ics"
FILE_CACHE = ROOT / "data" / "cache.json"
FILE_STATO = ROOT / "docs" / "stato.json"

ROMA = ZoneInfo("Europe/Rome")
LBA = "https://www.legabasket.it/api/championships"
EUROCUP = "https://api-live.euroleague.net/v2/competitions/U/seasons/U{anno}/games"
TRADUZIONI = {  # fasi EuroCup in italiano
    "Eighthfinals": "Ottavi di finale", "Quarterfinals": "Quarti di finale",
    "Semifinals": "Semifinali", "Finals": "Finale", "Final": "Finale",
    "Play-In": "Play-in", "Round": "Turno", "Game": "Gara",
}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; napoli-basket-calendar/1.0; +https://github.com)",
    "Accept": "application/json",
    "Referer": "https://www.legabasket.it/calendario",
}


# ------------------------------------------------------------------- utilità
def scarica_json(url, tentativi=3):
    errore = None
    for i in range(tentativi):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # rete, HTTP, JSON non valido
            errore = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"Impossibile scaricare {url}: {errore}")


def anno_stagione(oggi=None):
    """La stagione 2026/27 ha codice 2026: da luglio in poi si passa alla nuova."""
    oggi = oggi or datetime.now(ROMA)
    return oggi.year if oggi.month >= 7 else oggi.year - 1


def nome_breve(nome):
    return "Napoli" if SQUADRA.lower() in (nome or "").lower() else (nome or "Da definire")


def e_napoli(nome):
    return SQUADRA.lower() in (nome or "").lower()


# ------------------------------------------------------------ Lega Basket (LBA)
def partite_lba(anno):
    """Tutte le competizioni LBA della stagione (esclusa la Next Gen giovanile)."""
    elenco = scarica_json(f"{LBA}/get-championships?s={anno}&items=1000")
    partite = []
    for camp in elenco.get("competitions", []):
        if camp.get("cserie_code") == "NG":
            continue
        base = scarica_json(f"{LBA}/get-championships-calendar-by-id?id={camp['id']}")
        giornate = (base.get("filters") or {}).get("days") or []
        if not giornate:  # competizione senza giornate: usa le partite già presenti
            pagine = [base]
        else:
            pagine = [
                scarica_json(f"{LBA}/get-championships-calendar-by-id?id={camp['id']}&d={g['event_serial']}")
                for g in giornate
            ]
        for pagina in pagine:
            for m in pagina.get("matches") or []:
                if e_napoli(m.get("h_team_name")) or e_napoli(m.get("v_team_name")):
                    partite.append(evento_lba(m, camp))
    return partite


def nome_competizione_lba(camp):
    serie = camp.get("cserie_code")
    tipo = camp.get("ctype_code")
    if serie == "A1" and tipo == "RS":
        return "Serie A"
    if serie == "A1":
        return f"Serie A – {camp.get('ctype_name') or camp.get('full_name')}"
    return camp.get("full_name") or camp.get("cserie_name") or "Lega Basket"


def evento_lba(m, camp):
    inizio = datetime.fromisoformat(m["match_datetime"].replace(".000", ""))
    # Orario 00:00 = la Lega non ha ancora fissato l'orario: evento "tutto il giorno"
    da_definire = m.get("match_hh") in (0, None) and m.get("match_mm") in (0, None)
    luogo = ", ".join(x for x in [m.get("plant_name"), m.get("town_name")] if x)
    giocata = str(m.get("game_status")) == "2"
    return {
        "uid": f"lba-{m['id']}@napoli-basket-calendario",
        "competizione": nome_competizione_lba(camp),
        "turno": m.get("day_name") or "",
        "casa": m.get("h_team_name"),
        "ospite": m.get("v_team_name"),
        "inizio": inizio.date().isoformat() if da_definire else inizio.astimezone(timezone.utc).isoformat(),
        "tutto_il_giorno": da_definire,
        "luogo": luogo,
        "punteggio": [m.get("home_final_score"), m.get("visitor_final_score")] if giocata else None,
        "link": "https://www.legabasket.it/calendario",
    }


# ------------------------------------------------------------------- EuroCup
def partite_eurocup(anno):
    dati = scarica_json(EUROCUP.format(anno=anno))
    partite = []
    for g in dati.get("data", dati if isinstance(dati, list) else []):
        casa, ospite = g.get("local", {}).get("club", {}), g.get("road", {}).get("club", {})
        if CODICE_EUROCUP not in (casa.get("code"), ospite.get("code")) and not (
            e_napoli(casa.get("name")) or e_napoli(ospite.get("name"))
        ):
            continue
        partite.append(evento_eurocup(g, casa, ospite))
    return partite


def evento_eurocup(g, casa, ospite):
    inizio = datetime.fromisoformat(g["utcDate"].replace("Z", "+00:00"))
    da_definire = g.get("confirmedHour") is False
    fase = (g.get("phaseType") or {}).get("name") or ""
    turno = g.get("roundName") or ""
    if fase and fase != "Regular Season":
        turno = f"{fase} – {turno}" if turno else fase
    turno = re.sub(r"\b(" + "|".join(TRADUZIONI) + r")\b", lambda x: TRADUZIONI[x.group(1)], turno)
    venue = g.get("venue") or {}
    luogo = ", ".join(x for x in [venue.get("name"), venue.get("address")] if x)
    giocata = bool(g.get("played"))
    return {
        "uid": f"eurocup-{g.get('identifier') or g.get('id')}@napoli-basket-calendario",
        "competizione": "EuroCup",
        "turno": turno,
        "casa": casa.get("name"),
        "ospite": ospite.get("name"),
        "inizio": inizio.astimezone(ROMA).date().isoformat() if da_definire else inizio.isoformat(),
        "tutto_il_giorno": da_definire,
        "luogo": luogo,
        "punteggio": [g["local"].get("score"), g["road"].get("score")] if giocata else None,
        "link": "https://www.euroleaguebasketball.net/eurocup/",
    }


# ----------------------------------------------------------------------- iCal
def escape(testo):
    return (str(testo).replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\n", "\\n"))


def piega(riga):
    """Le righe iCal non devono superare 75 byte (RFC 5545)."""
    dati = riga.encode("utf-8")
    if len(dati) <= 75:
        return riga
    parti, corrente = [], b""
    for ch in riga:
        b = ch.encode("utf-8")
        limite = 75 if not parti else 74
        if len(corrente) + len(b) > limite:
            parti.append(corrente.decode("utf-8"))
            corrente = b""
        corrente += b
    parti.append(corrente.decode("utf-8"))
    return "\r\n ".join(parti)


def fmt_utc(dt):
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def titolo(ev):
    casa, ospite = nome_breve(ev["casa"]), nome_breve(ev["ospite"])
    if ev["punteggio"] and None not in ev["punteggio"]:
        partita = f"{casa} {ev['punteggio'][0]}-{ev['punteggio'][1]} {ospite}"
    else:
        partita = f"{casa} – {ospite}"
    return f"{EMOJI} {partita} ({ev['competizione']})"


def crea_ics(eventi):
    stamp = "20260101T000000Z"  # fisso: il file cambia solo quando cambiano le partite
    righe = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Napoli Basket Calendario//IT",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape(NOME_CALENDARIO)}",
        "X-WR-TIMEZONE:Europe/Rome",
        "X-WR-CALDESC:Serie A\\, coppe e EuroCup del Napoli Basket. Aggiornato in automatico.",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
        "X-PUBLISHED-TTL:PT6H",
    ]
    for ev in sorted(eventi, key=lambda e: e["inizio"]):
        descr = [ev["competizione"]]
        if ev["turno"]:
            descr.append(ev["turno"])
        descr.append(f"{ev['casa']} – {ev['ospite']}")
        if ev["tutto_il_giorno"]:
            descr.append("Orario non ancora ufficiale: verrà aggiornato appena la lega lo comunica.")
        if ev["link"]:
            descr.append(ev["link"])
        righe += ["BEGIN:VEVENT", f"UID:{ev['uid']}", f"DTSTAMP:{stamp}"]
        if ev["tutto_il_giorno"]:
            giorno = datetime.fromisoformat(ev["inizio"]).date()
            righe += [
                f"DTSTART;VALUE=DATE:{giorno:%Y%m%d}",
                f"DTEND;VALUE=DATE:{giorno + timedelta(days=1):%Y%m%d}",
                "TRANSP:TRANSPARENT",
            ]
            nome = titolo(ev) + " – orario da definire"
        else:
            inizio = datetime.fromisoformat(ev["inizio"])
            righe += [f"DTSTART:{fmt_utc(inizio)}", f"DTEND:{fmt_utc(inizio + DURATA_PARTITA)}"]
            nome = titolo(ev)
        righe.append(f"SUMMARY:{escape(nome)}")
        if ev["luogo"]:
            righe.append(f"LOCATION:{escape(ev['luogo'])}")
        righe.append(f"DESCRIPTION:{escape(chr(10).join(descr))}")
        if ev["link"]:
            righe.append(f"URL:{ev['link']}")
        righe.append("END:VEVENT")
    righe.append("END:VCALENDAR")
    return "\r\n".join(piega(r) for r in righe) + "\r\n"


# ----------------------------------------------------------------------- main
def main():
    anno = anno_stagione()
    cache = json.loads(FILE_CACHE.read_text("utf-8")) if FILE_CACHE.exists() else {}
    if cache.get("stagione") != anno:
        cache = {"stagione": anno}

    fonti = {"lba": partite_lba, "eurocup": partite_eurocup}
    esito = {}
    for nome, funzione in fonti.items():
        try:
            partite = funzione(anno)
            if not partite and cache.get(nome):
                raise RuntimeError("nessuna partita trovata (risposta vuota)")
            cache[nome] = partite
            esito[nome] = f"ok ({len(partite)} partite)"
        except Exception as e:
            esito[nome] = f"ERRORE, uso i dati salvati: {e}"
        print(f"{nome}: {esito[nome]}")

    eventi = [ev for nome in fonti for ev in cache.get(nome, [])]
    if not eventi:
        print("Nessuna partita disponibile: il calendario non viene sovrascritto.")
        return 1

    nuovo_ics = crea_ics(eventi)
    FILE_ICS.parent.mkdir(parents=True, exist_ok=True)
    FILE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    dati_ics = nuovo_ics.encode("utf-8")
    cambiato = not FILE_ICS.exists() or FILE_ICS.read_bytes() != dati_ics
    if cambiato:
        FILE_ICS.write_bytes(dati_ics)
    FILE_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), "utf-8")

    # stato.json: aggiornato quando cambia qualcosa o almeno una volta a settimana,
    # così GitHub non sospende l'automazione per inattività (60 giorni).
    ora = datetime.now(timezone.utc)
    stato = json.loads(FILE_STATO.read_text("utf-8")) if FILE_STATO.exists() else {}
    ultimo = datetime.fromisoformat(stato["ultimo_controllo"]) if "ultimo_controllo" in stato else None
    if cambiato or ultimo is None or ora - ultimo > timedelta(days=7) or stato.get("fonti") != esito:
        FILE_STATO.write_text(json.dumps({
            "ultimo_controllo": ora.isoformat(timespec="seconds"),
            "stagione": f"{anno}/{str(anno + 1)[-2:]}",
            "partite": len(eventi),
            "fonti": esito,
        }, ensure_ascii=False, indent=1), "utf-8")

    print(f"Calendario {'aggiornato' if cambiato else 'invariato'}: {len(eventi)} partite.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
