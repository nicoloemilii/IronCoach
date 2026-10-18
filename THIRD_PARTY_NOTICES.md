# Dipendenze e licenze di terzi

Il codice del prodotto non attribuisce diritti sui marchi o sulle integrazioni dei fornitori. Le dipendenze sono installate separatamente dal lockfile; le loro licenze originali rimangono nei pacchetti distribuiti. Il titolare deve verificare le condizioni applicabili al proprio utilizzo e alla distribuzione.

| Pacchetto | Versione | Licenza dichiarata nei metadati |
|---|---|---|
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.15.1 | MIT |
| argon2-cffi | 25.1.0 | MIT |
| argon2-cffi-bindings | 26.1.0 | MIT |
| certifi | 2026.7.22 | MPL-2.0 |
| cffi | 2.1.1 | MIT-0 |
| charset-normalizer | 3.5.2 | MIT |
| click | 8.5.0 | BSD-3-Clause |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| fastapi | 0.143.3 | MIT |
| garmin-fit-sdk | 21.218.0 | LicenseRef-FIT-Protocol-License-Agreement |
| h11 | 0.16.0 | MIT |
| idna | 3.20 | BSD-3-Clause |
| opentelemetry-api | 1.45.1 | Apache-2.0 |
| pwdlib | 0.3.1 | Vedi LICENSE nel pacchetto |
| pycparser | 3.11 | BSD-3-Clause |
| pydantic | 2.14.1 | MIT |
| pydantic-core | 2.50.1 | MIT |
| requests | 2.34.2 | Apache-2.0 |
| sqlalchemy | 2.1.5 | MIT |
| starlette | 1.8.0 | BSD-3-Clause |
| stripe | 16.0.0 | Vedi LICENSE nel pacchetto |
| typing-extensions | 4.16.0 | PSF-2.0 |
| typing-inspection | 0.4.4 | MIT |
| tzdata | 2026.5 | Apache-2.0 |
| urllib3 | 2.8.0 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |

Il Garmin FIT SDK usa `LicenseRef-FIT-Protocol-License-Agreement`, una licenza propria: non va trattata come una licenza open source generica o come autorizzazione Garmin Connect cloud. Vedere la licenza originale inclusa nel pacchetto `garmin-fit-sdk` e `GARMIN.md`.

Le dipendenze dei test web sono registrate in `web/tests/package-lock.json` e non vengono incluse nell’immagine del prodotto. La licenza commerciale del prodotto e le informazioni societarie restano da definire dal titolare.
