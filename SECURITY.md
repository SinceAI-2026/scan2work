# Security

Do not report passwords, API keys, bearer tokens, private scans or inspection
notes in a public issue or pull request. Use this repository’s **Security → Report
a vulnerability** form for a private security report. Include the affected revision,
minimal reproduction steps and expected impact; omit working credentials.

The project is a hackathon prototype. Use synthetic data for evaluation. The
hosted demo shares inspection jobs between visitors, while room layouts stay in
the browser. Do not use demo checklists or scene geometry as authorization for
physical work.

Keep `.env`, `secrets.env`, `board.env`, token files and private keys outside Git.
Use separate credentials for each deployment and rotate any exposed credential.
Configure your own domains, cloud account and storage before using the deployment
examples. See [hosted runtime](docs/public-demo.md) for the access boundaries.
