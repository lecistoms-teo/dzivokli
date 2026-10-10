# Dzīvokļu uzraugs → Telegram

Ik pēc ~10 minūtēm pārbauda jaunākos dzīvokļu sludinājumus **ss.com** un **city24.lv**.
Kad parādās sludinājums, kas atbilst kādam no taviem filtriem, tu saņem ziņu Telegram:
ar adresi, istabām, platību, stāvu, cenu, €/m² un saiti.

Filtru skaits nav ierobežots. Tie atrodas failā `config.yaml`.

## Filtru mainīšana
Atver `config.yaml` → zīmulis ✏️ → maini → **Commit changes**. Rīks uzreiz pārbauda sludinājumus no jauna.
Faila beigās ir visu pieejamo lauku saraksts (cena, €/m², platība, istabas, stāvs, ne pēdējais stāvs, sērija, apkaimes, atslēgvārdi u.c.).
Filtru var īslaicīgi izslēgt ar `enabled: false`.

## Telegram dati
**Settings → Secrets and variables → Actions**:
- `TELEGRAM_BOT_TOKEN` – bota žetons no @BotFather
- `TELEGRAM_CHAT_ID` – tavs ID no @userinfobot (vairākiem – ar komatu)

Šie dati nav redzami nevienam citam, arī publiskā repozitorijā.

## Manuāla palaišana
**Actions → Dzīvokļu uzraugs → Run workflow**.
Pirmajā reizē saņemsi ziņu «✅ Dzīvokļu uzraugs darbojas!» un līdz 3 pašreizējiem atbilstošiem sludinājumiem kā paraugu.

## Svarīgi zināt
- GitHub plānotās palaišanas mēdz kavēties par 5–15 minūtēm.
- Ja nāk sarkans ❌, atver palaišanu Actions cilnē un skaties kļūdu. Ja viens avots nestrādā, otrs turpina.
- **pp.lv** nav iekļauts, jo tā noteikumi aizliedz automatizētu lasīšanu.
