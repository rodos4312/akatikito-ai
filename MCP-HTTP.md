# MCP μέσω HTTP

Το Akatikito υποστηρίζει τοπικούς stdio servers και Streamable HTTP servers.
Οι συνδέσεις γίνονται από τις Ρυθμίσεις, μετά από ρητή έγκριση του ιδιοκτήτη.
Δεν ξεκινά σύνδεση επειδή το ζήτησε ένα μοντέλο, έγγραφο ή ιστοσελίδα.

Στο `data/mcp.json`, παράδειγμα χωρίς πραγματικά μυστικά:

```json
{
  "servers": {
    "remote": {
      "transport": "http",
      "url": "https://mcp.example.com/mcp",
      "headersEnv": {"Authorization": "AKATIKITO_REMOTE_AUTH"},
      "readOnlyTools": ["lookup"],
      "modes": ["ask", "assistant", "code", "debug"],
      "timeout": 20
    }
  }
}
```

Η μεταβλητή `AKATIKITO_REMOTE_AUTH` πρέπει να υπάρχει στο περιβάλλον εκκίνησης
της εφαρμογής, με ολόκληρη την τιμή του header, π.χ. `Bearer ...`. Τα `headers`
επιτρέπουν επίσης σταθερές τιμές που ορίζει ο ιδιοκτήτης. Στην έγκριση φαίνονται
μόνο η διεύθυνση και τα ονόματα των headers. Δεν επαναχρησιμοποιούνται κλειδιά
του επιλεγμένου παρόχου μοντέλου. Αυτόματη ροή OAuth δεν περιλαμβάνεται.

Για τοπικό server επιτρέπεται `http://127.0.0.1:8765/mcp`. Για άλλους προορισμούς
απαιτείται HTTPS, με κανονική επαλήθευση πιστοποιητικού. Το URL δεν δέχεται
userinfo, query ή fragment με credentials· χρησιμοποίησε headers. Ανακατευθύνσεις
απορρίπτονται χωρίς προώθηση των credentials.

## Συμπεριφορά μεταφοράς

- Έκδοση 2026-07-28: discovery, metadata και αντίστοιχα HTTP headers ανά αίτημα.
  Υποστηρίζονται αποκρίσεις JSON και SSE με notifications και τελική απόκριση.
- Συμβατότητα 2025-03-26, 2025-06-18 και 2025-11-25: initialize/initialized,
  protocol header, session ID όπου παρέχεται και DELETE κατά το κλείσιμο.
  Η παλιά μεταφορά δύο endpoints HTTP+SSE του 2024 δεν περιλαμβάνεται.
- Υποστηρίζονται `x-mcp-header` δηλώσεις σε στατικά προσπελάσιμες ιδιότητες
  εργαλείων: ασφαλής κωδικοποίηση, nested properties, null/απόντα πεδία και
  ασφαλές εύρος ακεραίων. Μη έγκυρες δηλώσεις αποκλείουν το αντίστοιχο εργαλείο
  και εμφανίζουν προειδοποίηση. Οι δηλώσεις μεταφοράς δεν φορτώνονται στο μοντέλο.
- Stop και timeout διακόπτουν τη σύνδεση/ροή. Δεν επαναλαμβάνονται αυτόματα
  εργαλεία μετά από σφάλμα, ληγμένη συνεδρία ή αβέβαιη μεταφορά. Απαιτείται
  επανασύνδεση μετά από κλείσιμο μεταφοράς. Η ακύρωση δεν αναιρεί ενέργειες που
  ήδη ολοκληρώθηκαν στον απομακρυσμένο server.
- Έως 2 MiB ανά απόκριση/ροή και 200 εργαλεία ανά server. Το περιεχόμενο server
  παραμένει εξωτερικό δεδομένο. Read-only απαιτεί και επιλογή του ιδιοκτήτη στο
  `readOnlyTools` και αντίστοιχο hint του server. Τα υπόλοιπα εργαλεία εξακολουθούν
  να ζητούν έγκριση και δεν αποκτούν επιπλέον δικαιώματα λόγω HTTP.
- Δεν ενεργοποιούνται sampling, elicitation, MRTR input requests ή subscriptions.
  Ο client δεν καλεί μοντέλο για να απαντήσει σε αιτήματα του MCP server.

Η υλοποίηση ελέγχθηκε με πραγματικούς τοπικούς HTTP/SSE servers και συνθετικά
credentials. Δεν έγινε δοκιμή πραγματικού παρόχου ή ζωντανής υπηρεσίας MCP.

Πρωτογενείς προδιαγραφές: [Streamable HTTP 2026](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/transports/streamable-http.mdx),
[μεταφορές 2025](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2025-11-25/basic/transports.mdx),
[version negotiation](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/versioning.mdx).
