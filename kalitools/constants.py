"""Domain metadata for Kali tools (categories, descriptions, mappings)."""

from __future__ import annotations

CATEGORY_ICONS = {
    'web': '🌐',
    'wireless': '📡',
    'forensics': '🧪',
    'exploitation': '💥',
    'password': '🔐',
    'recon': '🔍',
    'sniffing': '📡',
    'reverse': '🔧',
    'social': '🎣',
    'database': '🗄️',
    'crypto': '🔐',
    'network': '🌐',
    'vuln-scan': '🔍',
    'other': '🧰'
}

CATEGORY_NAMES = {
    'web': 'Web Applications',
    'wireless': 'Wireless',
    'forensics': 'Forensics',
    'exploitation': 'Exploitation',
    'password': 'Password Attacks',
    'recon': 'Reconnaissance',
    'sniffing': 'Sniffing/Spoofing',
    'reverse': 'Reverse Engineering',
    'social': 'Social Engineering',
    'database': 'Database',
    'crypto': 'Cryptography',
    'network': 'Network Tools',
    'vuln-scan': 'Vulnerability Scanning',
    'other': 'Other'
}

CATEGORIES = {
    'web': ['burpsuite', 'zaproxy', 'ffuf', 'dirb', 'dirsearch', 'feroxbuster', 'nikto', 'wpscan', 'cewl', 'gobuster',
            'wfuzz', 'commix', 'sqlmap', 'xsser', 'skipfish', 'whatweb', 'wafw00f', 'httprint', 'cadaver', 'davtest'],
    'wireless': ['aircrack-ng', 'wifite', 'reaver', 'kismet', 'mdk4', 'bettercap', 'fern-wifi-cracker', 'pixiewps',
                 'wash', 'airodump-ng', 'airmon-ng', 'aireplay-ng', 'fluxion', 'cowpatty', 'asleap'],
    'forensics': ['autopsy', 'bulk-extractor', 'sleuthkit', 'volatility', 'foremost', 'chainsaw', 'binwalk',
                  'scalpel', 'ddrescue', 'guymager', 'dc3dd', 'ewf-tools', 'extundelete', 'photorec', 'safecopy'],
    'exploitation': ['metasploit-framework', 'exploitdb', 'beef-xss', 'routersploit', 'set', 'social-engineer-toolkit',
                     'armitage', 'crackmapexec', 'powersploit', 'veil', 'shellter', 'searchsploit', 'empire'],
    'password': ['john', 'hashcat', 'hydra', 'medusa', 'crunch', 'hashid', 'hash-identifier', 'ophcrack',
                 'rainbowcrack', 'rsmangler', 'patator', 'thc-hydra', 'ncrack', 'chntpw', 'cmospwd'],
    'recon': ['nmap', 'amass', 'masscan', 'theharvester', 'dnsrecon', 'enum4linux', 'recon-ng', 'sublist3r', 'naabu',
              'dnsenum', 'fierce', 'dmitry', 'maltego', 'spiderfoot', 'shodan', 'subfinder', 'assetfinder', 'findomain',
              'whatweb', 'wafw00f', 'hping3', 'unicornscan', 'zenmap', 'autorecon'],
    'sniffing': ['wireshark', 'tcpdump', 'ettercap', 'bettercap', 'dsniff', 'netsniff-ng', 'tshark', 'arpspoof',
                 'dnsspoof', 'responder', 'mitmproxy', 'sslstrip', 'ngrep', 'driftnet'],
    'reverse': ['ghidra', 'radare2', 'gdb', 'apktool', 'jadx', 'binwalk', 'dex2jar', 'jd-gui', 'edb-debugger',
                'rizin', 'cutter', 'objdump', 'strings', 'ltrace', 'strace', 'valgrind'],
    'social': ['set', 'gophish', 'king-phisher', 'beef-xss', 'social-engineer-toolkit', 'evilginx2', 'modlishka'],
    'database': ['sqlmap', 'odat', 'mssqlclient', 'nosqlmap', 'bbqsql', 'jsql-injection', 'sqlninja', 'hexorbase'],
    'other': []
}

CATEGORY_DESCRIPTIONS: dict[str, str] = {
    'web': 'Web application scanning, content discovery, HTTP enumeration, CMS auditing (e.g., ffuf, nikto, zaproxy).',
    'wireless': '802.11/Wi‑Fi recon, packet capture, WPA/WPS attacks, rogue AP and injection tooling.',
    'forensics': 'Disk, memory and artifact analysis: carving, timeline reconstruction, evidence extraction.',
    'exploitation': 'Exploit frameworks and active attack tooling (payload orchestration, client-side attack surfaces).',
    'password': 'Credential attacks & recovery: hash identification, offline cracking, online brute forcing, wordlist generation.',
    'recon': 'Discovery of assets/services: subdomains, ports, DNS, OSINT collection, surface enumeration.',
    'sniffing': 'Traffic capture and protocol analysis: packet inspection, MiTM facilitation, flow monitoring.',
    'reverse': 'Disassembly, decompilation, debugging, firmware analysis, binary inspection.',
    'social': 'Phishing and social engineering campaign orchestration (SET, gophish, king-phisher).',
    'database': 'Database exploitation & auditing: SQL injection automation, Oracle/MSSQL/NoSQL assessment.',
    'other': 'Miscellaneous utilities and multi-domain tools not categorized elsewhere.'
}

CATEGORY_DEFAULT_SUBCATEGORY = {
    'web': 'General',
    'wireless': 'General',
    'forensics': 'General',
    'exploitation': 'General',
    'password': 'General',
    'recon': 'General',
    'sniffing': 'General',
    'reverse': 'General',
    'social': 'General',
    'database': 'General',
    'crypto': 'General',
    'network': 'General',
    'vuln-scan': 'General',
    'other': 'Misc',
}

SUBCATEGORY_MAP: dict[str, dict[str, str]] = {
    'web': {
        'ffuf': 'Fuzzing', 'dirb': 'Discovery', 'dirsearch': 'Discovery', 'feroxbuster': 'Discovery', 'gobuster': 'Discovery',
        'nikto': 'Scanning', 'zaproxy': 'Proxy/Scan', 'burpsuite': 'Proxy/Scan', 'wpscan': 'CMS', 'cewl': 'Wordlists',
        'wfuzz': 'Fuzzing', 'commix': 'Injection', 'sqlmap': 'SQLi', 'xsser': 'XSS', 'skipfish': 'Scanner',
        'whatweb': 'Fingerprint', 'wafw00f': 'Firewall', 'httprint': 'Fingerprint'
    },
    'wireless': {
        'aircrack-ng': 'Capture/Crack', 'wifite': 'Automation', 'kismet': 'Monitoring', 'reaver': 'WPS', 'mdk4': 'DoS/Attacks',
        'bettercap': 'MiTM', 'bettercap-ui': 'MiTM', 'fern-wifi-cracker': 'Automation', 'pixiewps': 'WPS', 'wash': 'WPS',
        'airodump-ng': 'Capture', 'airmon-ng': 'Interface', 'aireplay-ng': 'Injection', 'fluxion': 'Social',
        'bettercap-caplets': 'MiTM'
    },
    'forensics': {
        'volatility': 'Memory', 'autopsy': 'Disk/FS', 'sleuthkit': 'Disk/FS', 'foremost': 'Carving', 'bulk-extractor': 'Carving',
        'chainsaw': 'Windows', 'binwalk': 'Firmware', 'scalpel': 'Carving', 'ddrescue': 'Recovery', 'guymager': 'Imaging',
        'dc3dd': 'Imaging', 'ewf-tools': 'Imaging', 'extundelete': 'Recovery', 'photorec': 'Recovery', 'binwalk3': 'Firmware'
    },
    'exploitation': {
        'metasploit-framework': 'Framework', 'beef-xss': 'Client-Side', 'routersploit': 'IoT/Router', 'exploitdb': 'Database',
        'searchsploit': 'Database', 'social-engineer-toolkit': 'Social', 'set': 'Social', 'armitage': 'GUI',
        'crackmapexec': 'AD/SMB', 'powersploit': 'PowerShell', 'veil': 'Evasion', 'shellter': 'Evasion'
    },
    'password': {
        'hashcat': 'Offline', 'john': 'Offline', 'hydra': 'Online', 'medusa': 'Online', 'crunch': 'Wordlists',
        'hashid': 'Identification', 'hash-identifier': 'Identification', 'ophcrack': 'Windows', 'rainbowcrack': 'Tables',
        'cewl': 'Wordlists', 'rsmangler': 'Wordlists', 'patator': 'Online', 'thc-hydra': 'Online', 'ncrack': 'Online',
        'blue-hydra': 'Bluetooth'
    },
    'recon': {
        'amass': 'Subdomains', 'sublist3r': 'Subdomains', 'theharvester': 'OSINT', 'nmap': 'Port Scan', 'masscan': 'Port Scan',
        'naabu': 'Port Scan', 'enum4linux': 'SMB/AD', 'dnsrecon': 'DNS', 'dnsenum': 'DNS', 'fierce': 'DNS',
        'dmitry': 'OSINT', 'maltego': 'OSINT', 'recon-ng': 'Framework', 'spiderfoot': 'OSINT', 'shodan': 'OSINT',
        'censys': 'OSINT', 'subfinder': 'Subdomains', 'assetfinder': 'Subdomains', 'findomain': 'Subdomains',
        'autorecon': 'Automation'
    },
    'sniffing': {
        'tcpdump': 'Capture', 'wireshark': 'Analysis', 'ettercap': 'MiTM', 'dsniff': 'MiTM', 'netsniff-ng': 'Capture',
        'tshark': 'Capture', 'arpspoof': 'Spoofing', 'dnsspoof': 'Spoofing', 'responder': 'LLMNR/NBT-NS',
        'mitmproxy': 'HTTP Proxy', 'burpsuite': 'HTTP Proxy', 'sslstrip': 'SSL Strip'
    },
    'reverse': {
        'ghidra': 'Disassembler', 'radare2': 'Disassembler', 'gdb': 'Debugger', 'binwalk': 'Firmware', 'apktool': 'Android',
        'jadx': 'Android', 'dex2jar': 'Android', 'jd-gui': 'Java', 'ida-free': 'Disassembler', 'ollydbg': 'Debugger',
        'edb-debugger': 'Debugger', 'hopper': 'Disassembler', 'rizin': 'Disassembler', 'cutter': 'GUI', 'binwalk3': 'Firmware'
    },
    'social': {
        'set': 'Framework', 'gophish': 'Phishing', 'king-phisher': 'Phishing', 'beef-xss': 'Browser',
        'social-engineer-toolkit': 'Framework', 'evilginx2': 'Phishing', 'modlishka': 'Phishing'
    },
    'database': {
        'sqlmap': 'SQLi', 'odat': 'Oracle', 'mssqlclient': 'MSSQL', 'nosqlmap': 'NoSQL', 'bbqsql': 'Blind SQLi',
        'jsql-injection': 'SQLi', 'sqlninja': 'MSSQL', 'mongodb': 'NoSQL'
    },
    'other': {
        'atftp': 'File Transfer', 'axel': 'Download', 'azurehound': 'Cloud/AD', 'b374k': 'Web Shell',
        'bed': 'Fuzzing', 'berate-ap': 'Wireless', 'bind9': 'DNS Server', 'bing-ip2hosts': 'Recon',
        'bloodhound': 'AD Mapping', 'bloodhound-ce-python': 'AD Mapping', 'bloodhound-py': 'AD Mapping',
        'bloodvac': 'AD Mapping', 'bluelog': 'Bluetooth', 'blueranger': 'Bluetooth', 'bluesnarfer': 'Bluetooth',
        'bluez': 'Bluetooth', 'bopscrk': 'Wordlists'
    }
}

TOOL_DESCRIPTIONS = {
    'amass': 'In-depth subdomain enumeration',
    'ffuf': 'Fast web fuzzer',
    'gobuster': 'Directory/file & DNS bruteforcer',
    'masscan': 'Ultra-fast port scanner',
    'tcpdump': 'Command-line packet analyzer',
    '7zip': 'File archiver with high compression',
    'volatility': 'Memory forensics framework',
    'reaver': 'WPS brute-force attack tool',
    'bettercap': 'Network monitoring & attack tool',
    'crackmapexec': 'Network enumeration & exploitation',
    'foremost': 'File recovery forensics tool',
    'theharvester': 'OSINT & email harvesting',
    'enum4linux': 'Windows/Samba enumeration',
    'sleuthkit': 'Digital investigation analysis',
    'medusa': 'Fast parallel password cracker',
    'crunch': 'Wordlist generator',
    'hashid': 'Hash type identifier',
    'sublist3r': 'Subdomain enumeration tool',
    'dsniff': 'Network auditing & testing suite',
    'netsniff-ng': 'High-performance network toolkit',
    'gdb': 'GNU debugger for programs',
    'objdump': 'Display object file information',
    'strings': 'Find printable strings in files',
    'ltrace': 'Library call tracer',
    'gophish': 'Phishing campaign framework',
    'king-phisher': 'Phishing campaign toolkit',
    'mssqlclient': 'Microsoft SQL client tool',
    'odat': 'Oracle database attack tool',
    'nosqlmap': 'NoSQL database exploitation',
    'feroxbuster': 'Fast content discovery tool',
    'dirsearch': 'Web path scanner',
    'kismet': 'Wireless network detector',
    'mdk4': 'WiFi testing & DoS tool',
    'wifite': 'Automated WiFi auditing',
    'exploitdb': 'Exploit database archive',
    'searchsploit': 'Exploit database search tool',
    'bulk-extractor': 'Digital forensics evidence tool'
}


CATEGORY_KEYWORD_HINTS = {
    'web': ['web', 'http', 'browser', 'sql', 'xss', 'cms', 'dirb', 'gobuster', 'zaproxy'],
    'wireless': ['wifi', 'wireless', '802.11', 'bluetooth', 'wpa', 'wps', 'aircrack', 'rfid'],
    'forensics': ['forensic', 'memory', 'disk', 'image', 'carve', 'artifact', 'volatility', 'sleuthkit'],
    'exploitation': ['exploit', 'framework', 'payload', 'exploitdb', 'metasploit', 'shellcode'],
    'password': ['password', 'hash', 'brute', 'crack', 'wordlist', 'rainbow', 'hydra', 'john'],
    'recon': ['scan', 'recon', 'enum', 'discover', 'osint', 'subdomain', 'nmap', 'amass'],
    'sniffing': ['packet', 'sniff', 'capture', 'mitm', 'network monitor', 'pcap', 'wireshark', 'ettercap'],
    'reverse': ['reverse', 'disassemble', 'binary', 'debug', 'firmware', 'ghidra', 'radare'],
    'social': ['phish', 'campaign', 'social engineer', 'gophish', 'setoolkit', 'credential harvest'],
    'database': ['database', 'oracle', 'sql', 'mssql', 'mongodb', 'nosql', 'sqlmap'],
}


SUBCATEGORY_KEYWORD_HINTS = {
    'web': {
        'Fuzzing': ['fuzz', 'ffuf', 'wfuzz'],
        'Discovery': ['dir', 'enum', 'gobuster', 'dirb', 'ferox'],
        'SQLi': ['sql', 'database', 'sqli', 'blind'],
        'Proxy/Scan': ['proxy', 'browser', 'zaproxy', 'burp'],
    },
    'password': {
        'Offline': ['hashcat', 'john', 'offline'],
        'Online': ['hydra', 'medusa', 'ssh', 'ftp'],
        'Wordlists': ['wordlist', 'crunch', 'cewl'],
    },
    'recon': {
        'Subdomains': ['subdomain', 'dns', 'amass', 'sublist3r', 'findomain'],
        'Port Scan': ['port scan', 'nmap', 'masscan', 'naabu'],
        'OSINT': ['osint', 'harvest', 'theharvester', 'shodan'],
    },
    'forensics': {
        'Memory': ['memory', 'ram', 'volatility'],
        'Disk/FS': ['disk', 'image', 'sleuthkit', 'autopsy'],
        'Carving': ['carve', 'foremost', 'scalpel'],
    },
    'wireless': {
        'Capture/Crack': ['capture', 'crack', 'aircrack', 'airodump'],
        'Automation': ['automate', 'wifite', 'fern'],
        'Bluetooth': ['bluetooth', 'bt', 'blue'],
    },
    'exploitation': {
        'Framework': ['framework', 'metasploit', 'routersploit'],
        'Client-Side': ['browser', 'beef'],
        'Evasion': ['evasion', 'veil', 'shellter'],
    },
}


# Mapping of Kali meta-packages to canonical category slugs. Used to infer
# categories by inspecting dependencies of official meta groups via apt-cache.
META_CATEGORY_SOURCES = {
    'kali-tools-information-gathering': ('recon', ''),
    'kali-tools-recon': ('recon', ''),  # alias in older releases
    'kali-tools-web': ('web', ''),
    'kali-tools-vulnerability': ('vuln-scan', ''),
    'kali-tools-wireless': ('wireless', ''),
    'kali-tools-802-11': ('wireless', 'Capture/Crack'),
    'kali-tools-bluetooth': ('wireless', 'Bluetooth'),
    'kali-tools-rfid': ('wireless', 'RFID'),
    'kali-tools-sdr': ('wireless', 'SDR'),
    'kali-tools-voip': ('network', ''),
    'kali-tools-hardware': ('network', ''),
    'kali-tools-passwords': ('password', ''),
    'kali-tools-crypto-stego': ('crypto', ''),
    'kali-tools-database': ('database', ''),
    'kali-tools-sniffing-spoofing': ('sniffing', ''),
    'kali-tools-forensics': ('forensics', ''),
    'kali-tools-post-exploitation': ('exploitation', ''),
    'kali-tools-exploitation': ('exploitation', ''),
    'kali-tools-reverse-engineering': ('reverse', ''),
    'kali-tools-social-engineering': ('social', ''),
    'kali-tools-reporting': ('other', ''),
    'kali-tools-fuzzing': ('web', 'Fuzzing'),
    'kali-tools-passwords-rainbowcrack': ('password', 'Offline'),
    'kali-tools-passwords-hydra': ('password', 'Online'),
    'kali-tools-passwords-cracking': ('password', ''),
}


def get_category_description(cat: str | None) -> str | None:
    if not cat:
        return None
    return CATEGORY_DESCRIPTIONS.get(cat)


def get_subcategory_for(name: str, category: str | None) -> str:
    cat = (category or 'other').lower()
    mapping = SUBCATEGORY_MAP.get(cat, {})
    return mapping.get(name, '')


def get_category_display_name(category: str | None) -> str:
    if not category:
        return 'Other'
    return CATEGORY_NAMES.get(category.lower(), category.title())
