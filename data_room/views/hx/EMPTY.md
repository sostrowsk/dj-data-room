# `hx_empty` — Dokumentenliste neu laden

HTMX-GET-Endpunkt, der beim Schliessen des Upload-Modals (Abbrechen / X) aufgerufen wird.

## URL

`GET /data-room/hx/<pk>/empty/` → `data_room:hx-empty`

## Ablauf

Rendert `_show_protected_documents.html` als HTMX-Partial-Swap und aktualisiert so die gesamte Dokumentenliste nach dem Schliessen des Modals.

## Request-Ablauf

1. **Guards**: OTP, HTMX-Header und Projektberechtigung erforderlich
2. **Sortierung**: liest `?sort_documents=` (name, -name, date, -date)
3. **Sichtbarkeitsfilter** je Benutzertyp:
   - Broker/Admin: sehen alle Dokumente
   - Client: eigene Dokumente + freigegebene Broker-/Partner-Dokumente
   - Partner: nur Dokumente der eigenen Gesellschaft + freigegebene Client-/Broker-Dokumente
4. **Rendert** `_show_protected_documents.html` mit Dokument-Querysets, Firmendokumenten und `not_partner`-Flag

## Template-Einbindung

```html
hx-get="{% url 'data_room:hx-empty' project.pk %}"
hx-target="#upload-documents" hx-swap="outerHTML"
```

Verwendet von den Schliessen-/Abbrechen-Buttons in `_hx_upload_protected_document.html`.

## Hinweis

Der Name "empty" ist irrefuehrend — der Endpunkt liefert eine vollstaendige Dokumentenliste zurueck, keinen leeren Container.
