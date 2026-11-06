# OSS Control Center (`fadi-oss-tui`)

Antarmuka terminal (TUI) untuk **Alibaba Cloud OSS** di atas [ossutil 2.x](https://www.alibabacloud.com/help/en/oss/developer-reference/ossutil-overview).
Dibuat untuk upload besar (puluhan sampai ratusan GB) yang memakan waktu berjam-jam: bisa dijeda dan dilanjutkan, antrean tetap ada setelah crash, dan setiap upload diverifikasi.

> Status: awal (v0.2.0), hanya Windows. Tidak berafiliasi dengan Alibaba Cloud.

[English](README.md)

## Yang sudah bisa

- **Dark Operations Console.** Dashboard dengan navigasi samping, metrik antrean langsung, daftar upload, detail pekerjaan terpilih, dan ringkasan transfer. Tema charcoal dengan aksen lime, tetap sepenuhnya berjalan di terminal.
- **Login sekali, tetap masuk.** Isi AccessKey, region, dan bucket atau prefix yang diizinkan, misalnya `oss://nama-bucket/EGO/`. Aplikasi mengetes lokasi tersebut (hanya-baca), menjadikannya root workspace, dan membatasi browser serta tujuan upload agar tetap di dalam prefix itu. Logout kapan saja dengan `L`.
- **Upload file besar** dengan jeda, lanjut (juga setelah aplikasi atau PC berhenti), kecepatan dan ETA langsung, serta pemeriksaan setelah upload.
- **Menjelajah bucket** (folder dan file, paging, detail) dan memilih tujuan upload dari browser.
- **Menangkap salah ketik di tujuan.** Kalau folder tujuan belum ada, dialog pre-flight memberi tahu dan menyarankan folder mirip yang sudah ada (misalnya `team-alpha-black` ketika kamu mengetik `team-alpah-black`).

Belum ada: upload folder atau batch, hapus / ganti nama / pindah, beberapa profil, sistem operasi lain.

## Kebutuhan

- Windows 10 atau 11 (kredensial dilindungi dengan Windows DPAPI)
- Python 3.11 atau lebih baru (dikembangkan dan diuji dengan 3.14)
- ossutil 2.x (diunduh dan diverifikasi SHA-256 secara otomatis oleh installer)
- [Windows Terminal](https://aka.ms/terminal) disarankan agar tampilan paling baik

## Pasang

Klik dua kali `install.cmd`, atau dari terminal di folder ini:

```powershell
.\install.cmd -AddToPath
```

| Opsi | Efek |
|---|---|
| `-AddToPath` | Tambahkan folder launcher ke PATH pengguna, sehingga `fadi-oss-tui` jalan di terminal mana pun |
| `-NoShortcut` | Jangan buat pintasan Start Menu |
| `-InstallDir <path>` | Pasang di tempat lain (default `%LOCALAPPDATA%\FaDi-OSS-TUI`) |
| `-Python <python.exe>` | Pakai Python tertentu |

Installer hanya untuk penggunamu (tanpa hak administrator), memakai virtual environment pribadi, otomatis mengunduh ossutil 2.4.0 ke folder aplikasi, lalu memverifikasi SHA-256-nya. Layar login mendeteksi executable bawaan; kamu tidak perlu mencari path atau memasang ossutil sendiri. Pembungkus `.cmd` menjalankan PowerShell dengan `-ExecutionPolicy Bypass` hanya untuk skrip itu; baca `install.ps1` dulu kalau ingin tahu persis yang dikerjakannya.

## Jalankan

- Start Menu: **OSS Control Center**, atau
- `run.cmd` di folder ini, atau
- `fadi-oss-tui` (kalau memakai `-AddToPath`, di terminal baru)

Saat pertama kali muncul layar login. Sesudahnya langsung ke Upload Manager.

## Tombol

**Upload Manager:** `A` tambah upload, `B` jelajah bucket, `S` mulai, `P` jeda, `R` lanjut, `V` verifikasi ulang, `X` hapus dari antrean, `L` logout, `Q` keluar (upload yang berjalan dijeda dulu).

**Browser:** `Enter` buka folder atau tampilkan detail file, `Backspace` naik, `M` muat lagi, `I` detail (ukuran total folder dihitung dari bucket dan bisa butuh beberapa detik), `R` segarkan, `S` pilih folder ini (saat dibuka dari dialog Add), `Esc` tutup.

## Cara kredensial ditangani

- Secret AccessKey dienkripsi dengan **Windows DPAPI** (terikat ke akun Windows-mu) dan disimpan di `profile.json`. Tidak pernah ditulis sebagai teks biasa.
- ossutil menerima kunci hanya lewat **environment variable proses miliknya**, bukan lewat baris perintah dan bukan lewat file konfigurasi. ossutil diberi file konfigurasi kosong, jadi `~/.ossutilconfig` lama tidak bisa ikut campur.
- Batasan: program lain yang berjalan sebagai akun Windows yang sama bisa mendekripsi secret, dan logout menghapus file tapi tidak menjamin data hilang secara fisik di SSD. Pakai RAM user dengan izin seminimal mungkin.

## Lokasi data

`%LOCALAPPDATA%\FaDi-OSS-TUI\` berisi `profile.json` (login), `queue.json` (antrean upload), `logs\`, `checkpoints\`, serta programnya (`venv\`, `bin\`, dan opsional `ossutil\`).

## Perlu diketahui

- Satu upload berjalan pada satu waktu.
- Jeda menghentikan proses ossutil; lanjut menjalankannya lagi dengan folder checkpoint yang sama. File di atas 100 MiB dilanjutkan dari checkpoint-nya; file yang lebih kecil dikirim ulang dari awal file itu.
- Verifikasi setelah upload memeriksa bahwa ukuran objek sama dan CRC64 ada. ossutil sendiri memeriksa CRC64 selama transfer; CRC64 file lokal tidak dihitung ulang dan dibandingkan.
- Upload tidak pernah memakai `-f`, jadi ossutil yang menentukan apa yang terjadi jika objek sudah ada (kasus ini belum diuji).
- Beberapa operasi mencetak error soal upload yang belum selesai bila RAM user tidak punya izin itu; aplikasi memperlakukannya sebagai peringatan.

## Copot

```powershell
.\uninstall.cmd                      # menghapus program, login dan antrean tetap ada
.\uninstall.cmd -RemoveData          # juga menghapus login, antrean, log, dan checkpoint (bertanya dulu)
```

Pencopot hanya menghapus berkas yang dibuat installer dan menolak menyentuh folder yang bukan hasil instalasi.

## Pengembangan

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
python -m pytest
```

Tes memakai proses ossutil palsu dan bucket palsu; tidak pernah menghubungi OSS. Tata letak berkas sama seperti pada README berbahasa Inggris.

## Lisensi

Belum dipilih. Sampai lisensi ditambahkan, semua hak dimiliki pemilik repositori.