# -*- coding: utf-8 -*-
"""
Winget Manager - Interface graphique pour winget.

Fonctionnalites:
  - Onglet « Mises a jour »: liste les MAJ, filtre, cocher/décocher,
    mettre a jour ou télécharger la sélection (barre de progression),
    épingler/ignorer, ouvrir le dossier.
  - Onglet « Catalogue »: rechercher des paquets, voir les détails,
    installer, télécharger, épingler.

Logique métier (appels winget) séparée de l'UI.
"""

import json
import os
import queue
import re
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
from tkinter import (
    Tk, ttk, StringVar, filedialog, messagebox, scrolledtext, Toplevel,
)

# sv_ttk (thème Windows 11 clair/sombre) — optionnel, fallback sur thème natif.
try:
    import sv_ttk
    HAS_SV_TTK = True
except ImportError:
    HAS_SV_TTK = False


def _app_dir():
    """Répertoire de l'application: à côté du .exe si figé (PyInstaller),
    sinon à côté du .py."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _resource_path(name):
    """Chemin d'une ressource (icône) qui fonctionne en script ET en exe figé."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


# --- Configuration ---------------------------------------------------------

APP_NAME = "Winget Manager"
APP_VERSION = "3.2"
GITHUB_URL = "https://github.com/CordaAvlao"
CONFIG_PATH = os.path.join(_app_dir(), "config.json")
ICON_PATH = _resource_path("icon.ico")
WINGET_BIN = os.environ.get("WINGET_BIN", "winget")

# Regex de progression: capture "21.0 MB / 67.1 MB".
PROGRESS_RE = re.compile(
    r"([\d.]+)\s*([KMG]?i?[Bo])\s*/\s*([\d.]+)\s*([KMG]?i?[Bo])", re.IGNORECASE
)
_UNIT_BYTES = {
    "o": 1, "b": 1,
    "kio": 1024, "ko": 1024, "kb": 1024,
    "mio": 1024 ** 2, "mo": 1024 ** 2, "mb": 1024 ** 2,
    "gio": 1024 ** 3, "go": 1024 ** 3, "gb": 1024 ** 3,
}


def _parse_progress(line):
    """Extrait (current_bytes, total_bytes) d'une ligne de progression winget."""
    m = PROGRESS_RE.search(line)
    if not m:
        return None
    try:
        cur = float(m.group(1)) * _UNIT_BYTES.get(m.group(2).lower(), 1)
        total = float(m.group(3)) * _UNIT_BYTES.get(m.group(4).lower(), 1)
    except (ValueError, KeyError):
        return None
    if total <= 0:
        return None
    return cur, total


# --- Logique métier --------------------------------------------------------

def _run_winget(args, log_queue=None):
    """Exécute winget avec les arguments donnés.

    Retourne (returncode, stdout). Envoie chaque ligne lue en temps réel
    dans log_queue (pour affichage dans l'UI), et détecte la progression.
    """
    cmd = [WINGET_BIN] + args
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError:
        msg = "winget est introuvable. Vérifiez qu'il est installé et dans le PATH."
        if log_queue is not None:
            log_queue.put(("error", msg))
        return -1, ""

    out_lines = []
    for line in proc.stdout:
        out_lines.append(line)
        if log_queue is not None:
            stripped = line.rstrip("\r\n")
            # Ignore les caractères du spinner (-, \, |, /) que winget affiche
            # en boucle pendant une opération (bruit visuel dans le journal).
            content = stripped.strip()
            if content and all(c in "-\\|/" for c in content):
                continue
            prog = _parse_progress(stripped)
            if prog is not None:
                log_queue.put(("progress", prog))
            elif content:  # ignore aussi les lignes vides
                log_queue.put(("line", content))
    proc.wait()
    return proc.returncode, "".join(out_lines)


def _parse_table(stdout, expected_sources=("winget", "msstore")):
    """Découpe la sortie texte d'un tableau winget (search/upgrade).

    Repère l'en-tête via la ligne de séparateurs '---', puis les positions
    des en-têtes donnent les bornes de découpage des colonnes.
    Retourne une liste de dicts {name, id, version, source}.
    """
    lines = stdout.splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        if "ID" in line and "winget" not in line.lower() and i + 1 < len(lines):
            if set(lines[i + 1].strip()) == {"-"}:
                header_idx = i
                break
    if header_idx is None:
        return []

    header = lines[header_idx]
    starts = [m.start() for m in re.finditer(r"\S+", header)]
    if len(starts) < 3:
        return []

    # Colonnes détectées: généralement Nom, ID, Version, Source (mais l'ordre/nombre varie).
    # On mappe par nom d'en-tête localisé → on s'appuie sur les positions.
    columns = [(starts[i], starts[i + 1] if i + 1 < len(starts) else len(header))
               for i in range(len(starts))]

    results = []
    for line in lines[header_idx + 2:]:
        if not line.strip():
            continue
        cells = []
        for i, (s, e) in enumerate(columns):
            if i + 1 < len(columns):
                cells.append(line[s:columns[i + 1][0]].strip())
            else:
                cells.append(line[s:].strip())
        # Détecte l'ID: cellule contenant un point avec un format type Publisher.Name
        id_cell = ""
        source_cell = ""
        for c in cells:
            if re.match(r"^[\w.-]+\.[\w.-]+$", c) and "." in c and not id_cell:
                id_cell = c
        if cells:
            source_cell = cells[-1]
        if not id_cell:
            continue
        # Filtre par source si demandé.
        if source_cell.lower() not in expected_sources and source_cell:
            # On accepte aussi les lignes où la source n'est pas précisée.
            pass
        name = cells[0] if cells else id_cell
        # Version: cellule contenant des chiffres.
        version = ""
        for c in cells[1:]:
            if re.search(r"\d", c) and c != id_cell and c.lower() not in expected_sources:
                version = c
                break
        results.append({
            "id": id_cell,
            "name": name,
            "version": version or "—",
            "source": source_cell if source_cell.lower() in expected_sources else "winget",
        })
    return results


def list_upgrades(log_queue=None):
    """Retourne la liste des mises à jour disponibles."""
    rc, stdout = _run_winget(
        ["upgrade", "--accept-source-agreements"], log_queue
    )
    rows = _parse_table(stdout)
    packages = []
    for r in rows:
        # _parse_table renvoie une seule version; pour upgrade on a 2 colonnes
        # (Version actuelle + Disponible). On les sépare après coup.
        packages.append(r)
    # Amélioration: pour upgrade, on récupère Actuelle -> Disponible.
    return _parse_upgrade_versions(stdout, packages)


def _parse_upgrade_versions(stdout, packages):
    """Extrait les versions actuelle/disponible du tableau upgrade."""
    lines = stdout.splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        if "ID" in line and "winget" not in line.lower() and i + 1 < len(lines):
            if set(lines[i + 1].strip()) == {"-"}:
                header_idx = i
                break
    if header_idx is None:
        # Fallback: on ne sépare pas.
        for p in packages:
            p.setdefault("current", "—")
            p.setdefault("available", p.get("version", "—"))
        return packages

    header = lines[header_idx]
    starts = [m.start() for m in re.finditer(r"\S+", header)]
    # Pour upgrade il y a 5 colonnes: Nom, ID, Version, Disponible, Source.
    out = []
    data_lines = [l for l in lines[header_idx + 2:] if l.strip()]
    for idx, p in enumerate(packages):
        if idx < len(data_lines):
            line = data_lines[idx]
            # Version = 3e, Disponible = 4e colonne.
            if len(starts) >= 5:
                current = line[starts[2]:starts[3]].strip()
                avail = line[starts[3]:starts[4]].strip()
                p["current"] = current or "—"
                p["available"] = avail or "—"
            else:
                p["current"] = "—"
                p["available"] = p.get("version", "—")
        else:
            p["current"] = "—"
            p["available"] = p.get("version", "—")
        out.append(p)
    return out


def search_packages(query, log_queue=None):
    """Recherche des paquets via winget search."""
    rc, stdout = _run_winget(
        ["search", query, "--accept-source-agreements"], log_queue
    )
    return _parse_table(stdout)


def show_package(pkg_id, log_queue=None):
    """Récupère les détails d'un paquet (winget show) sous forme de dict."""
    rc, stdout = _run_winget(
        ["show", "--id", pkg_id, "-e", "--accept-source-agreements"], log_queue
    )
    details = {}
    for line in stdout.splitlines():
        # Lignes au format "  Champ: valeur"
        m = re.match(r"\s*([A-Za-zÀ-ÿ ]+):\s*(.*)", line)
        if m:
            key = m.group(1).strip().lower()
            val = m.group(2).strip()
            if val:
                details[key] = val
    return details


def update_package(pkg_id, log_queue=None):
    """Met à jour un paquet via winget upgrade --id <id> -e."""
    return _run_winget(
        ["upgrade", "--id", pkg_id, "-e",
         "--accept-package-agreements", "--accept-source-agreements"],
        log_queue,
    )


def download_package(pkg_id, folder, log_queue=None):
    """Télécharge l'installateur d'un paquet dans le dossier choisi."""
    return _run_winget(
        ["download", "--id", pkg_id, "-e", "-d", folder,
         "--accept-source-agreements"],
        log_queue,
    )


def install_package(pkg_id, log_queue=None):
    """Installe un paquet via winget install --id <id> -e."""
    return _run_winget(
        ["install", "--id", pkg_id, "-e",
         "--accept-package-agreements", "--accept-source-agreements"],
        log_queue,
    )


def pin_package(pkg_id, log_queue=None):
    """Épingle un paquet (il n'apparaîtra plus dans les MAJ)."""
    return _run_winget(["pin", "add", "--id", pkg_id, "-e"], log_queue)


def unpin_package(pkg_id, log_queue=None):
    """Retire l'épinglage d'un paquet."""
    return _run_winget(["pin", "remove", "--id", pkg_id, "-e"], log_queue)


def find_existing_files(pkg_name, pkg_id, folder):
    """Détecte les fichiers déjà téléchargés pour un paquet dans le dossier."""
    if not os.path.isdir(folder):
        return []
    prefix_base = pkg_name or pkg_id
    cleaned = re.sub(r'[<>:"/\\|?*]', "", prefix_base).strip()
    matches = []
    for fname in os.listdir(folder):
        if fname.lower().startswith(cleaned.lower()):
            matches.append(fname)
    return matches


# --- Décodeur de codes d'erreur -------------------------------------------

# Codes d'erreur winget/MSIX les plus fréquents, mappés à un conseil clair.
# Les clés sont les codes entiers (négatifs ou positifs selon le contexte).
WINGET_ERRORS = {
    # Droits administrateur / accès refusé
    -1073741811: ("Accès refusé (droits insuffisants)",
                  "Cette mise à jour nécessite les droits administrateur. "
                  "Cliquez sur « Relancer en tant qu'administrateur »."),
    0x80070005: ("Accès refusé (E_ACCESSDENIED)",
                 "L'opération requiert des privilèges administrateur."),
    5: ("Accès refusé",
        "Droits administrateur nécessaires. Relancez l'app en admin."),
    # Framework / package en cours d'utilisation
    0x8A150014: ("Framework utilisé par une application en cours",
                 "Le composant ne peut pas être mis à jour car une application "
                 "qui en dépend est en cours d'exécution. Fermez vos applications "
                 "(ou redémarrez le PC) puis réessayez."),
    0x80073D3F: ("Package en cours d'utilisation (MSIX)",
                 "Fermez l'application liée à ce paquet et réessayez. "
                 "Un redémarrage du PC peut aider."),
    # Paquet déjà installé / aucune version disponible
    0x8A150022: ("Aucune version applicable trouvée",
                 "Aucune mise à jour disponible pour ce paquet sur cette architecture."),
    0x8A150029: ("Aucune application correspondante",
                 "Le paquet n'a pas pu être trouvé ou traité par winget."),
    # Hash / intégrité
    0x8A15002F: ("Échec de la vérification de hachage",
                 "L'empreinte du téléchargement ne correspond pas (risque de corruption "
                 "ou de modification). Vous pouvez réessayer plus tard."),
    # Réseau / source
    0x8A150031: ("Source introuvable",
                 "La source winget est inaccessible. Vérifiez votre connexion réseau."),
    -2147024891: ("Erreur réseau / source indisponible",
                  "Vérifiez votre connexion internet et réessayez."),
    # Disque / espace
    0x80070070: ("Espace disque insuffisant",
                 "Libérez de l'espace sur votre disque puis réessayez."),
    0x80070020: ("Fichier en cours d'utilisation",
                 "Le fichier d'installation est verrouillé par un processus. "
                 "Fermez les applications concernées."),
    # Code d'installation générique MSI
    1602: ("Installation annulée par l'utilisateur (MSI)",
           "L'installateur a été interrompu."),
    1603: ("Erreur fatale d'installation (MSI)",
           "L'installation a échoué. Essayez en mode administrateur, "
           "ou fermez les applications en cours."),
    1618: ("Une autre installation est en cours (MSI)",
           "Attendez que les autres installations se terminent, puis réessayez."),
    # winget no upgrade found (n'est pas vraiment une erreur)
    -1978335212: ("Aucune application trouvée (0x8A150014)",
                  "Ce n'est généralement pas une vraie erreur — souvent un paquet "
                  "sans version exploitable. Vous pouvez l'ignorer."),
}


def decode_winget_error(rc):
    """Traduit un code retour winget en (titre, conseil) ou None si inconnu."""
    # winget renvoie parfois le code en négatif (complément à 2 sur 32 bits).
    normalized = rc & 0xFFFFFFFF
    # Recherche avec le code normalisé (hex) et le code brut.
    for key, value in WINGET_ERRORS.items():
        if key == rc or (key & 0xFFFFFFFF) == normalized:
            return value
    return None


def format_error_message(rc):
    """Retourne un message d'erreur lisible pour un code retour."""
    decoded = decode_winget_error(rc)
    if decoded:
        title, tip = decoded
        return f"code {rc} — {title}. {tip}"
    return f"code {rc} (code hex: 0x{rc & 0xFFFFFFFF:08X})"


def is_admin_error(rc):
    """Détecte si un code retour indique un besoin d'élévation (droits admin)."""
    decoded = decode_winget_error(rc)
    if decoded:
        title = decoded[0].lower()
        if "accès refusé" in title or "administrateur" in title or "droits" in title:
            return True
    return rc in (-1073741811, 0x80070005, 5)


def open_folder(path):
    """Ouvre un dossier dans l'Explorateur Windows."""
    try:
        if os.path.isdir(path):
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            os.startfile(os.path.dirname(path) or ".")  # type: ignore[attr-defined]
    except Exception:
        try:
            subprocess.Popen(["explorer", path])
        except Exception:
            pass


# --- Configuration persistence --------------------------------------------

def load_config():
    """Charge la configuration depuis config.json."""
    defaults = {
        "download_folder": os.path.join(os.path.expanduser("~"), "Downloads"),
        "theme": "light",
    }
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                defaults.update(json.load(fh))
        except (json.JSONDecodeError, OSError):
            pass
    return defaults


def save_config(cfg):
    """Sauvegarde la configuration."""
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2, ensure_ascii=False)
    except OSError:
        pass


# --- UI --------------------------------------------------------------------

class WingetManagerApp:
    CHECK_OFF = "☐"
    CHECK_ON = "☑"
    STATES = {"ok": "✅", "fail": "❌", "skip": "⏭", "pending": "…", "pin": "📌"}

    def __init__(self, root):
        self.root = root
        self.root.title(f"{APP_NAME} {APP_VERSION}")
        self.root.geometry("980x680")
        self.root.minsize(820, 560)
        if os.path.isfile(ICON_PATH):
            try:
                self.root.iconbitmap(default=ICON_PATH)
            except Exception:
                pass

        self.config = load_config()
        self.log_queue = queue.Queue()
        self.packages = []           # MAJ affichées (filtrées)
        self.all_packages = []       # toutes les MAJ (pour le filtre)
        self.checked = set()         # index (dans packages) cochés
        self.busy = False
        self.worker_thread = None
        self.theme = self.config.get("theme", "light")
        self.cancel_all = False
        # Résultats du catalogue.
        self.catalog = []            # liste de dicts {id,name,version,source}

        self._build_ui()
        self._apply_theme()
        self._poll_log_queue()
        # Charge la liste initiale.
        self.root.after(100, self.refresh)

    # --- Construction de l'interface --------------------------------------

    def _build_ui(self):
        # Notebook (onglets).
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=(8, 0))

        self.upgrades_tab = ttk.Frame(self.notebook)
        self.catalog_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.upgrades_tab, text="  Mises à jour  ")
        self.notebook.add(self.catalog_tab, text="  Catalogue  ")

        self._build_upgrades_tab()
        self._build_catalog_tab()
        self._build_bottom_bar()

    def _build_upgrades_tab(self):
        tab = self.upgrades_tab

        # Barre supérieure: actualiser + filtre + compte.
        top = ttk.Frame(tab, padding=(10, 8))
        top.pack(fill="x")
        ttk.Button(top, text="Actualiser", command=self.refresh).pack(side="left")
        ttk.Label(top, text="  Filtrer :").pack(side="left", padx=(12, 0))
        self.filter_var = StringVar()
        self.filter_var.trace_add("write", self._apply_filter)
        ttk.Entry(top, textvariable=self.filter_var, width=28).pack(side="left", padx=4)
        self.count_var = StringVar(value="—")
        ttk.Label(top, textvariable=self.count_var, padding=(12, 0)).pack(side="left")

        # Tableau.
        tree_frame = ttk.Frame(tab, padding=(10, 4))
        tree_frame.pack(fill="both", expand=True)
        cols = ("check", "name", "id", "version", "state")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=14)
        self.tree.heading("check", text="")
        self.tree.heading("name", text="Nom")
        self.tree.heading("id", text="ID")
        self.tree.heading("version", text="Actuelle → Disponible")
        self.tree.heading("state", text="État")
        self.tree.column("check", width=40, anchor="center", stretch=False)
        self.tree.column("name", width=240, anchor="w")
        self.tree.column("id", width=250, anchor="w")
        self.tree.column("version", width=200, anchor="w")
        self.tree.column("state", width=70, anchor="center")
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<space>", self._toggle_selected)

        # Sélection + dossier.
        sel = ttk.Frame(tab, padding=(10, 2))
        sel.pack(fill="x")
        ttk.Button(sel, text="Tout cocher", command=self._check_all).pack(side="left")
        ttk.Button(sel, text="Tout décocher", command=self._uncheck_all).pack(side="left", padx=(6, 0))

        folder_f = ttk.Frame(tab, padding=(10, 4))
        folder_f.pack(fill="x")
        ttk.Label(folder_f, text="Dossier de téléchargement :").pack(side="left")
        self.folder_var = StringVar(value=self.config["download_folder"])
        ttk.Entry(folder_f, textvariable=self.folder_var).pack(side="left", fill="x", expand=True, padx=(8, 6))
        ttk.Button(folder_f, text="Parcourir…", command=self._browse_folder).pack(side="left")
        ttk.Button(folder_f, text="Ouvrir", command=self._open_download_folder).pack(side="left", padx=(6, 0))

        # Actions MAJ.
        act = ttk.Frame(tab, padding=(10, 4))
        act.pack(fill="x")
        self.update_btn = ttk.Button(act, text="Mettre à jour la sélection", command=self._do_update)
        self.update_btn.pack(side="left")
        self.download_btn = ttk.Button(act, text="Télécharger la sélection", command=self._do_download)
        self.download_btn.pack(side="left", padx=(8, 0))
        self.pin_btn = ttk.Button(act, text="Épingler la sélection", command=self._do_pin)
        self.pin_btn.pack(side="left", padx=(8, 0))

    def _build_catalog_tab(self):
        tab = self.catalog_tab

        top = ttk.Frame(tab, padding=(10, 8))
        top.pack(fill="x")
        ttk.Label(top, text="Rechercher :").pack(side="left")
        self.search_var = StringVar()
        search_entry = ttk.Entry(top, textvariable=self.search_var, width=40)
        search_entry.pack(side="left", padx=(8, 8))
        search_entry.bind("<Return>", lambda e: self._do_search())
        self.search_btn = ttk.Button(top, text="Rechercher", command=self._do_search)
        self.search_btn.pack(side="left")

        tree_frame = ttk.Frame(tab, padding=(10, 4))
        tree_frame.pack(fill="both", expand=True)
        cols = ("name", "id", "version", "source")
        self.cat_tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=16)
        self.cat_tree.heading("name", text="Nom")
        self.cat_tree.heading("id", text="ID")
        self.cat_tree.heading("version", text="Version")
        self.cat_tree.heading("source", text="Source")
        self.cat_tree.column("name", width=300, anchor="w")
        self.cat_tree.column("id", width=300, anchor="w")
        self.cat_tree.column("version", width=120, anchor="w")
        self.cat_tree.column("source", width=100, anchor="w")
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.cat_tree.yview)
        self.cat_tree.configure(yscrollcommand=vsb.set)
        self.cat_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        # Double-clic = détails.
        self.cat_tree.bind("<Double-1>", self._show_details)

        # Actions catalogue (sur le paquet sélectionné).
        act = ttk.Frame(tab, padding=(10, 4))
        act.pack(fill="x")
        self.cat_install_btn = ttk.Button(act, text="Installer ce paquet", command=self._install_selected)
        self.cat_install_btn.pack(side="left")
        self.cat_download_btn = ttk.Button(act, text="Télécharger ce paquet", command=self._download_selected)
        self.cat_download_btn.pack(side="left", padx=(8, 0))
        self.cat_pin_btn = ttk.Button(act, text="Épingler ce paquet", command=self._pin_selected)
        self.cat_pin_btn.pack(side="left", padx=(8, 0))

        self.cat_count_var = StringVar(value="")
        ttk.Label(tab, textvariable=self.cat_count_var, padding=(10, 2)).pack(anchor="w")

    def _build_bottom_bar(self):
        # Barre commune: thème + admin + progression + journal.
        bar = ttk.Frame(self.root, padding=(10, 4))
        bar.pack(fill="x")
        self.theme_btn = ttk.Button(bar, text="🌙", width=4, command=self._toggle_theme)
        self.theme_btn.pack(side="right", padx=(6, 0))
        ttk.Button(bar, text="À propos", command=self._show_about).pack(side="right", padx=(6, 0))
        self.admin_btn = ttk.Button(bar, text="Relancer en admin", command=self._restart_as_admin)
        self.admin_btn.pack(side="right")
        self.admin_btn.pack_forget()

        # Barre de progression globale + libellé.
        prog = ttk.Frame(self.root, padding=(10, 2))
        prog.pack(fill="x")
        self.prog_bar = ttk.Progressbar(prog, length=400, mode="determinate")
        self.prog_bar.pack(side="left")
        self.prog_var = StringVar(value="")
        ttk.Label(prog, textvariable=self.prog_var, padding=(10, 0)).pack(side="left")

        # Journal.
        log_frame = ttk.LabelFrame(self.root, text="Journal", padding=4)
        log_frame.pack(fill="both", expand=False, padx=10, pady=(4, 10))
        self.log_widget = scrolledtext.ScrolledText(log_frame, height=8, wrap="word",
                                                    state="disabled", font=("Consolas", 9))
        self.log_widget.pack(fill="both", expand=True)
        self.log_widget.tag_configure("error", foreground="#c00000")
        self.log_widget.tag_configure("success", foreground="#008000")
        self.log_widget.tag_configure("info", foreground="#0050a0")
        self.log_widget.tag_configure("path", foreground="#0066cc", underline=True)
        self.log_widget.bind("<Button-1>", self._on_log_click)
        self.log_widget.tag_bind("path", "<Enter>",
                                 lambda e: self.log_widget.config(cursor="hand2"))
        self.log_widget.tag_bind("path", "<Leave>",
                                 lambda e: self.log_widget.config(cursor=""))

    # --- Gestion de l'état "occupé" ---------------------------------------

    def _set_busy(self, busy, buttons=None):
        self.busy = busy
        if buttons is None:
            buttons = (self.update_btn, self.download_btn, self.pin_btn,
                       self.cat_install_btn, self.cat_download_btn, self.cat_pin_btn,
                       self.search_btn)
        state = "disabled" if busy else "normal"
        for w in buttons:
            try:
                w.config(state=state)
            except Exception:
                pass
        self.root.cursor = "watch" if busy else ""

        if not busy:
            self.prog_bar.stop()
            self.prog_bar.config(mode="determinate", value=0, maximum=100)
            self.prog_var.set("")
            self.cancel_all = False
            # On garde les coches et états (contrairement à avant) pour laisser
            # l'utilisateur relancer ou ajuster. _refresh_check_display est appelé
            # si besoin explicitement.

    # --- Thème ------------------------------------------------------------

    def _apply_theme(self):
        if HAS_SV_TTK:
            sv_ttk.set_theme("dark" if self.theme == "dark" else "light")
        else:
            style = ttk.Style()
            try:
                style.theme_use("vista")
            except Exception:
                pass
        self.theme_btn.config(text="☀️" if self.theme == "dark" else "🌙")
        if self.theme == "dark":
            self.log_widget.tag_configure("error", foreground="#ff6b6b")
            self.log_widget.tag_configure("success", foreground="#69db7c")
            self.log_widget.tag_configure("info", foreground="#74c0fc")
            self.log_widget.tag_configure("path", foreground="#4dabf7", underline=True)
        else:
            self.log_widget.tag_configure("error", foreground="#c00000")
            self.log_widget.tag_configure("success", foreground="#008000")
            self.log_widget.tag_configure("info", foreground="#0050a0")
            self.log_widget.tag_configure("path", foreground="#0066cc", underline=True)

    def _toggle_theme(self):
        self.theme = "dark" if self.theme == "light" else "light"
        self.config["theme"] = self.theme
        save_config(self.config)
        self._apply_theme()

    # --- Affichage MAJ ----------------------------------------------------

    def _populate_tree(self, packages):
        self.tree.delete(*self.tree.get_children())
        self.all_packages = packages
        self.checked = set()
        self._apply_filter()

    def _apply_filter(self, *_args):
        text = self.filter_var.get().strip().lower()
        if text:
            filtered = [p for p in self.all_packages
                        if text in p["name"].lower() or text in p["id"].lower()]
        else:
            filtered = list(self.all_packages)
        self.packages = filtered
        self.checked = set()  # le filtre change les index
        self.tree.delete(*self.tree.get_children())
        for idx, pkg in enumerate(filtered):
            version = f"{pkg.get('current', '—')} → {pkg.get('available', '—')}"
            state = pkg.get("_state", "")
            self.tree.insert("", "end", iid=str(idx),
                             values=(self.CHECK_OFF, pkg["name"], pkg["id"], version, state))
        n = len(self.all_packages)
        nf = len(filtered)
        if text and nf != n:
            self.count_var.set(f"{nf}/{n} mise(s) à jour")
        else:
            self.count_var.set(f"{n} mise(s) à jour disponible(s)" if n else "Aucune mise à jour")

    def _refresh_check_display(self):
        for idx in range(len(self.packages)):
            mark = self.CHECK_ON if idx in self.checked else self.CHECK_OFF
            if self.tree.set(str(idx), "check") != mark:
                self.tree.set(str(idx), "check", mark)

    def _set_row_state(self, idx, state_key):
        """Marque l'état d'une ligne (ok/fail/skip/pending/pin)."""
        if idx < len(self.packages):
            self.packages[idx]["_state"] = self.STATES.get(state_key, "")
            self.tree.set(str(idx), "state", self.STATES.get(state_key, ""))

    # --- Interactions tableau MAJ -----------------------------------------

    def _on_tree_click(self, event):
        if self.busy:
            return
        if self.tree.identify("region", event.x, event.y) != "cell":
            return
        row = self.tree.identify_row(event.y)
        if row == "":
            return
        self._toggle(int(row))

    def _toggle_selected(self, event=None):
        if self.busy:
            return
        for item in self.tree.selection():
            self._toggle(int(item))

    def _toggle(self, idx):
        if idx in self.checked:
            self.checked.discard(idx)
            self.tree.set(str(idx), "check", self.CHECK_OFF)
        else:
            self.checked.add(idx)
            self.tree.set(str(idx), "check", self.CHECK_ON)

    def _check_all(self):
        if self.busy:
            return
        self.checked = set(range(len(self.packages)))
        self._refresh_check_display()

    def _uncheck_all(self):
        if self.busy:
            return
        self.checked = set()
        self._refresh_check_display()

    # --- Dossier ----------------------------------------------------------

    def _browse_folder(self):
        initial = self.folder_var.get() or os.path.expanduser("~")
        if not os.path.isdir(initial):
            initial = os.path.expanduser("~")
        folder = filedialog.askdirectory(initialdir=initial, title="Dossier de téléchargement")
        if folder:
            self.folder_var.set(folder)
            self.config["download_folder"] = folder
            save_config(self.config)

    def _open_download_folder(self):
        open_folder(self.folder_var.get())

    # --- Journal ----------------------------------------------------------

    def _poll_log_queue(self):
        try:
            while True:
                kind, payload = self.log_queue.get_nowait()
                if kind == "progress":
                    current, total = payload
                    pct = min(100, int(current / total * 100))
                    self.prog_bar.config(mode="determinate", maximum=100, value=pct)
                else:
                    self._log(payload + "\n", kind)
        except queue.Empty:
            pass
        self.root.after(80, self._poll_log_queue)

    def _log(self, message, kind="line"):
        self.log_widget.config(state="normal")
        try:
            ts = datetime.now().strftime("%H:%M:%S")
            tag = kind if kind in ("error", "success", "info", "path") else ()
            # Détecte les chemins de fichiers pour les rendre cliquables.
            if kind == "line" and re.search(r"[A-Za-z]:[\\/]", message):
                self.log_widget.insert("end", f"[{ts}] ", ())
                start = self.log_widget.index("end")
                self.log_widget.insert("end", message, ("path",))
                # Mémorise le chemin détecté pour le clic.
                m = re.search(r"[A-Za-z]:(?:[\\/][^\s:|<>\"\']*)+", message)
                if m:
                    self._last_path = m.group(0)
                return
            self.log_widget.insert("end", f"[{ts}] {message}", tag)
            self.log_widget.see("end")
        finally:
            self.log_widget.config(state="disabled")

    def _on_log_click(self, event):
        """Ouvre le chemin cliqué dans le journal."""
        idx = self.log_widget.index(f"@{event.x},{event.y}")
        tags = self.log_widget.tag_names(idx)
        if "path" in tags:
            path = getattr(self, "_last_path", None)
            if path and os.path.exists(path):
                open_folder(path)

    # --- Worker générique -------------------------------------------------

    def _run_worker(self, target):
        if self.busy:
            return
        self._set_busy(True)
        self.worker_thread = threading.Thread(target=target, daemon=True)
        self.worker_thread.start()

    def _selected_packages(self):
        return [self.packages[i] for i in sorted(self.checked)]

    def refresh(self):
        """Récupère la liste des mises à jour."""
        if self.busy:
            return
        self._log("Récupération des mises à jour disponibles…", "info")
        self._set_busy(True)

        def work():
            packages = list_upgrades(self.log_queue)
            self.root.after(0, lambda: self._on_refresh_done(packages))

        self.worker_thread = threading.Thread(target=work, daemon=True)
        self.worker_thread.start()

    def _on_refresh_done(self, packages):
        self._populate_tree(packages)
        self._set_busy(False)
        self._log(f"{len(packages)} mise(s) à jour trouvée(s).", "success")

    # --- Progression globale sur sélection --------------------------------

    def _global_progress_config(self, total):
        """Configure la barre pour une opération sur N paquets."""
        self.root.after(0, lambda: self.prog_bar.config(mode="determinate",
                                                        maximum=total * 100, value=0))

    def _global_progress_tick(self, done_count, total, sub_pct, label):
        """Avance la barre globale et met à jour le libellé."""
        value = (done_count * 100) + max(0, min(100, sub_pct))
        self.root.after(0, lambda: (
            self.prog_bar.config(value=value),
            self.prog_var.set(label),
        ))

    def _run_on_selection(self, verb, action_fn, pkg_field_msg, update_states=True,
                          refresh_after=False, is_download=False):
        """Exécute action_fn(id, folder?, log_queue) sur chaque paquet coché.

        - verb: mot affiché (« Mise à jour », « Téléchargement »).
        - action_fn: fonction métier.
        - update_states: marquer l'état de chaque ligne.
        """
        selected = self._selected_packages()
        if not selected:
            messagebox.showinfo(APP_NAME, "Aucune mise à jour cochée.")
            return
        folder = self.folder_var.get().strip() if is_download else None
        if is_download and (not folder or not os.path.isdir(folder)):
            messagebox.showwarning(APP_NAME, "Veuillez choisir un dossier de téléchargement valide.")
            return
        if is_download:
            self.config["download_folder"] = folder
            save_config(self.config)

        total = len(selected)
        self._global_progress_config(total)
        if not is_download:
            self.root.after(0, lambda: self.prog_bar.config(mode="indeterminate"))
            self.root.after(0, self.prog_bar.start)

        def work():
            any_admin = False
            for i, pkg in enumerate(selected):
                if self.cancel_all:
                    self._log(f"{verb} annulé par l'utilisateur.", "info")
                    break
                if update_states:
                    self.root.after(0, lambda i=i: self._set_row_state(i, "pending"))
                label = f"{verb} {i + 1}/{total} — {pkg['name']}"
                self.root.after(0, lambda l=label: self.prog_var.set(l))
                self._log(f"[{i + 1}/{total}] {verb} de {pkg['name']} ({pkg['id']})…", "info")

                # Téléchargement: gère fichier existant + barre déterminée.
                if is_download:
                    self._handle_download(pkg, folder, i, total)
                    if update_states:
                        self.root.after(0, lambda i=i: self._set_row_state(i, "ok"))
                    continue

                rc, _ = action_fn(pkg["id"], self.log_queue)
                if rc == 0:
                    self._log(f"✔ {pkg['name']} : {verb.lower()} effectué(e).", "success")
                    if update_states:
                        self.root.after(0, lambda i=i: self._set_row_state(i, "ok"))
                else:
                    self._log(f"✖ Échec ({verb.lower()}) de {pkg['name']} — {format_error_message(rc)}", "error")
                    if update_states:
                        self.root.after(0, lambda i=i: self._set_row_state(i, "fail"))
                    if is_admin_error(rc):
                        any_admin = True
            self.root.after(0, lambda: self._on_action_done(refresh=refresh_after, admin=any_admin))

        self._run_worker(work)

    def _handle_download(self, pkg, folder, idx, total):
        """Gère un téléchargement individuel (fichier existant + progression)."""
        existing = find_existing_files(pkg["name"], pkg["id"], folder)
        if existing:
            action = self._ask_overwrite(pkg["name"], existing)
            if action == "cancel":
                self.cancel_all = True
                return
            elif action == "skip":
                self._log(f"⏭ {pkg['name']} ignoré (fichier existant).", "info")
                self.root.after(0, lambda i=idx: self._set_row_state(idx, "skip"))
                return
            else:
                for fname in existing:
                    try:
                        os.remove(os.path.join(folder, fname))
                        self._log(f"  Supprimé : {fname}", "info")
                    except OSError as exc:
                        self._log(f"  Impossible de supprimer {fname}: {exc}", "error")
        self.root.after(0, lambda: self.prog_bar.config(mode="determinate",
                                                        maximum=100, value=0))
        self._log(f"  → {folder}", "info")
        rc, _ = download_package(pkg["id"], folder, self.log_queue)
        if rc == 0:
            self._log(f"✔ {pkg['name']} téléchargé.", "success")
            self.root.after(0, lambda i=idx: self._set_row_state(idx, "ok"))
        else:
            self._log(f"✖ Échec du téléchargement de {pkg['name']} — {format_error_message(rc)}", "error")
            self.root.after(0, lambda i=idx: self._set_row_state(idx, "fail"))

    def _do_update(self):
        self._run_on_selection("Mise à jour", update_package, None,
                               update_states=True, refresh_after=True)

    def _do_download(self):
        self._run_on_selection("Téléchargement", download_package, None,
                               update_states=True, is_download=True)

    def _do_pin(self):
        selected = self._selected_packages()
        if not selected:
            messagebox.showinfo(APP_NAME, "Aucune mise à jour cochée.")
            return

        def work():
            for i, pkg in enumerate(selected, 1):
                if self.cancel_all:
                    break
                self._log(f"[{i}/{len(selected)}] Épinglage de {pkg['name']}…", "info")
                rc, _ = pin_package(pkg["id"], self.log_queue)
                if rc == 0:
                    self._log(f"📌 {pkg['name']} épinglé.", "success")
                    self.root.after(0, lambda i=i - 1: self._set_row_state(i - 1, "pin"))
                else:
                    self._log(f"✖ Échec épinglage de {pkg['name']} — {format_error_message(rc)}", "error")
            self.root.after(0, lambda: self._on_action_done(refresh=True))

        self._run_worker(work)

    def _on_action_done(self, refresh=False, admin=False):
        self._set_busy(False)
        if admin:
            self.admin_btn.pack(side="right", padx=(6, 0))
            self._log("Certaines mises à jour nécessitent des droits administrateur.", "error")
        if refresh:
            self.refresh()

    # --- Catalogue --------------------------------------------------------

    def _do_search(self):
        query = self.search_var.get().strip()
        if not query:
            messagebox.showinfo(APP_NAME, "Saisissez un terme de recherche.")
            return

        def work():
            self._log(f"Recherche de « {query} »…", "info")
            results = search_packages(query, self.log_queue)
            self.root.after(0, lambda: self._on_search_done(results))
            self._log(f"{len(results)} résultat(s).", "success")

        self._run_worker(work)

    def _on_search_done(self, results):
        self.catalog = results
        self.cat_tree.delete(*self.cat_tree.get_children())
        for idx, r in enumerate(results):
            self.cat_tree.insert("", "end", iid=str(idx),
                                 values=(r["name"], r["id"], r["version"], r["source"]))
        self.cat_count_var.set(f"{len(results)} résultat(s)")

    def _selected_catalog_pkg(self):
        sel = self.cat_tree.selection()
        if not sel:
            messagebox.showinfo(APP_NAME, "Sélectionnez un paquet dans le catalogue.")
            return None
        return self.catalog[int(sel[0])]

    def _show_details(self, event=None):
        pkg = self._selected_catalog_pkg()
        if not pkg:
            return

        def work():
            self._log(f"Détails de {pkg['name']}…", "info")
            details = show_package(pkg["id"], self.log_queue)
            self.root.after(0, lambda: self._show_details_dialog(pkg, details))

        self._run_worker(work)

    def _show_details_dialog(self, pkg, details):
        # Construit un texte lisible des détails.
        lines = [f"Nom : {pkg['name']}", f"ID : {pkg['id']}", f"Version : {pkg['version']}"]
        field_map = {
            "publisher": "Éditeur", "auteur": "Auteur", "author": "Auteur",
            "homepage": "Site web", "page d'accueil": "Site web",
            "description": "Description", "publisher support url": "Support",
            "url du support de l'éditeur": "Support", "license": "Licence",
        }
        for key, val in details.items():
            label = None
            for fk, fl in field_map.items():
                if fk in key:
                    label = fl
                    break
            if label:
                lines.append(f"{label} : {val}")
        text = "\n\n".join(lines[:12])

        dlg = Toplevel(self.root)
        dlg.title(f"Détails — {pkg['name']}")
        dlg.geometry("520x420")
        dlg.transient(self.root)
        txt = scrolledtext.ScrolledText(dlg, wrap="word", font=("Segoe UI", 10), padx=14, pady=10)
        txt.pack(fill="both", expand=True)
        txt.insert("1.0", text)
        txt.config(state="disabled")
        ttk.Button(dlg, text="Fermer", command=dlg.destroy).pack(pady=8)

    def _install_selected(self):
        pkg = self._selected_catalog_pkg()
        if not pkg:
            return
        if not messagebox.askyesno(APP_NAME, f"Installer « {pkg['name']} » ?"):
            return

        def work():
            self._log(f"Installation de {pkg['name']} ({pkg['id']})…", "info")
            self.root.after(0, lambda: self.prog_bar.config(mode="indeterminate"))
            self.root.after(0, self.prog_bar.start)
            self.root.after(0, lambda: self.prog_var.set(f"Installation — {pkg['name']}"))
            rc, _ = install_package(pkg["id"], self.log_queue)
            if rc == 0:
                self._log(f"✔ {pkg['name']} installé.", "success")
            else:
                self._log(f"✖ Échec de l'installation de {pkg['name']} — {format_error_message(rc)}", "error")
            self.root.after(0, lambda: self._on_action_done(refresh=True))

        self._run_worker(work)

    def _download_selected(self):
        pkg = self._selected_catalog_pkg()
        if not pkg:
            return
        folder = self.folder_var.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showwarning(APP_NAME, "Dossier de téléchargement invalide.")
            return

        def work():
            existing = find_existing_files(pkg["name"], pkg["id"], folder)
            if existing:
                action = self._ask_overwrite(pkg["name"], existing)
                if action == "cancel":
                    return
                elif action != "skip":
                    for fname in existing:
                        try:
                            os.remove(os.path.join(folder, fname))
                        except OSError:
                            pass
            self.root.after(0, lambda: self.prog_bar.config(mode="determinate", maximum=100, value=0))
            self._log(f"Téléchargement de {pkg['name']} → {folder}", "info")
            rc, _ = download_package(pkg["id"], folder, self.log_queue)
            if rc == 0:
                self._log(f"✔ {pkg['name']} téléchargé.", "success")
            else:
                self._log(f"✖ Échec du téléchargement de {pkg['name']} — {format_error_message(rc)}", "error")
            self.root.after(0, lambda: self._on_action_done(refresh=False))

        self._run_worker(work)

    def _pin_selected(self):
        pkg = self._selected_catalog_pkg()
        if not pkg:
            return

        def work():
            rc, _ = pin_package(pkg["id"], self.log_queue)
            if rc == 0:
                self._log(f"📌 {pkg['name']} épinglé.", "success")
            else:
                self._log(f"✖ Échec épinglage de {pkg['name']} — {format_error_message(rc)}", "error")
            self.root.after(0, lambda: self._on_action_done(refresh=True))

        self._run_worker(work)

    # --- Notification fichier existant ------------------------------------

    def _ask_overwrite(self, pkg_name, existing_files):
        result = {"value": "skip"}
        done = threading.Event()
        files_txt = "\n".join(f"  • {f}" for f in existing_files[:5])
        if len(existing_files) > 5:
            files_txt += f"\n  • … (+{len(existing_files) - 5} autres)"

        def ask():
            answer = messagebox.askyesnocancel(
                APP_NAME,
                f"Le paquet « {pkg_name} » a déjà été téléchargé :\n\n"
                f"{files_txt}\n\n"
                f"Remplacer (Oui) · Ignorer ce paquet (Non) · Annuler tout (Annuler) ?"
            )
            result["value"] = "replace" if answer is True else ("skip" if answer is False else "cancel")
            done.set()

        self.root.after(0, ask)
        done.wait()
        return result["value"]

    # --- À propos ---------------------------------------------------------

    def _show_about(self):
        """Affiche la fenêtre À propos avec un lien cliquable vers GitHub."""
        dlg = Toplevel(self.root)
        dlg.title(f"À propos de {APP_NAME}")
        dlg.geometry("420x300")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        if os.path.isfile(ICON_PATH):
            try:
                dlg.iconbitmap(default=ICON_PATH)
            except Exception:
                pass

        # Icône.
        try:
            from tkinter import PhotoImage
            icon_img = PhotoImage(file=ICON_PATH)
            ttk.Label(dlg, image=icon_img).pack(pady=(16, 4))
            dlg._icon_ref = icon_img  # garder une référence
        except Exception:
            pass

        ttk.Label(dlg, text=f"{APP_NAME}", font=("Segoe UI", 16, "bold")).pack(pady=(2, 0))
        ttk.Label(dlg, text=f"Version {APP_VERSION}", font=("Segoe UI", 10)).pack()
        ttk.Label(dlg, text="Gestionnaire graphique pour winget",
                  font=("Segoe UI", 9)).pack(pady=(8, 2))

        # Lien GitHub cliquable.
        link = ttk.Label(dlg, text=GITHUB_URL, foreground="#0066cc",
                         font=("Segoe UI", 10, "underline"), cursor="hand2")
        link.pack(pady=4)
        link.bind("<Button-1>", lambda e: webbrowser.open(GITHUB_URL))

        ttk.Label(dlg, text="Mises à jour · Téléchargements · Catalogue",
                  font=("Segoe UI", 8), foreground="#666666").pack(pady=(8, 0))

        ttk.Button(dlg, text="Fermer", command=dlg.destroy).pack(side="bottom", pady=12)

    # --- Utilitaires ------------------------------------------------------

    def _restart_as_admin(self):
        try:
            import ctypes
            if getattr(sys, "frozen", False):
                exe, params = sys.executable, None
            else:
                exe, params = sys.executable, f'"{os.path.abspath(__file__)}"'
            if ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1) <= 32:
                messagebox.showerror(APP_NAME, "Élévation refusée.")
                return
            self.root.destroy()
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Impossible de relancer en admin: {exc}")


def main():
    root = Tk()
    WingetManagerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
