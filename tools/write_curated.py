#!/usr/bin/env python3
"""Author the curated core of the catalog.

The long tail can be machine-derived from APT metadata, but the tools people
actually reach for deserve hand-written summaries, real binary names, engagement
phases, alternatives and -- the point of the rebrand -- every route to installing
them, not just the Debian one.

Emits YAML into ``catalog/``. Re-runnable: it overwrites only the ids listed
here, so the seeded long tail is untouched.

    python tools/write_curated.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from loadout.catalog.compile import dump_tool  # noqa: E402
from loadout.model import InstallMethod, Tool  # noqa: E402

DEB = ("kali", "debian", "parrot", "ubuntu")
CHECKSUMS = "*checksums*.txt"


def apt(package: str, **kw):
    return {"provider": "apt", "package": package, "distros": list(DEB), **kw}


def brew(formula: str, **kw):
    return {"provider": "brew", "formula": formula, **kw}


def go(module: str, **kw):
    return {"provider": "go", "module": module, **kw}


def pipx(package: str, **kw):
    return {"provider": "pipx", "package": package, **kw}


def cargo(crate: str, **kw):
    return {"provider": "cargo", "crate": crate, **kw}


def gem(name: str, **kw):
    return {"provider": "gem", "gem": name, **kw}


def gh(repo: str, **kw):
    return {"provider": "github", "repo": repo, "checksums": CHECKSUMS, **kw}


# id, summary, categories, phases, binaries, tags, homepage, install, alternatives
CURATED: list[dict] = [
    # ---- reconnaissance ---------------------------------------------------
    dict(
        id="nmap", summary="Network discovery and service/version fingerprinting",
        categories=["recon"], phases=["discovery", "reconnaissance"],
        binaries=["nmap", "ncat", "nping"], tags=["port-scan", "classic"],
        homepage="https://nmap.org", license="NPSL", verify="nmap --version",
        install=[apt("nmap"), brew("nmap")],
        alternatives=["masscan", "naabu", "rustscan"],
    ),
    dict(
        id="masscan", summary="Internet-scale TCP port scanner",
        categories=["recon"], phases=["discovery"], binaries=["masscan"],
        tags=["port-scan"], homepage="https://github.com/robertdavidgraham/masscan",
        requires_root=True, verify="masscan --version",
        install=[apt("masscan"), brew("masscan")], alternatives=["nmap", "naabu"],
    ),
    dict(
        id="naabu", summary="Fast SYN/CONNECT port scanner built for pipelines",
        categories=["recon"], phases=["discovery"], binaries=["naabu"],
        tags=["port-scan", "projectdiscovery"],
        homepage="https://github.com/projectdiscovery/naabu",
        install=[
            apt("naabu"),
            go("github.com/projectdiscovery/naabu/v2/cmd/naabu@latest"),
            gh("projectdiscovery/naabu"),
        ],
        alternatives=["masscan", "nmap"],
    ),
    dict(
        id="amass", summary="In-depth attack surface mapping and subdomain enumeration",
        categories=["recon"], phases=["reconnaissance"], binaries=["amass"],
        tags=["subdomains", "osint", "owasp"], homepage="https://owasp.org/www-project-amass/",
        install=[apt("amass"), brew("amass"), gh("owasp-amass/amass")],
        alternatives=["subfinder", "assetfinder"],
    ),
    dict(
        id="subfinder", summary="Passive subdomain discovery across dozens of sources",
        categories=["recon"], phases=["reconnaissance"], binaries=["subfinder"],
        tags=["subdomains", "projectdiscovery"],
        homepage="https://github.com/projectdiscovery/subfinder",
        install=[
            apt("subfinder"),
            brew("subfinder"),
            go("github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest"),
            gh("projectdiscovery/subfinder"),
        ],
        alternatives=["amass", "assetfinder", "findomain"],
    ),
    dict(
        id="dnsx", summary="Fast DNS resolution and probing toolkit",
        categories=["recon"], phases=["reconnaissance"], binaries=["dnsx"],
        tags=["dns", "projectdiscovery"],
        homepage="https://github.com/projectdiscovery/dnsx",
        install=[
            apt("dnsx"),
            go("github.com/projectdiscovery/dnsx/cmd/dnsx@latest"),
            gh("projectdiscovery/dnsx"),
        ],
        alternatives=["dnsrecon", "massdns"],
    ),
    dict(
        id="theharvester", summary="OSINT gathering of emails, hosts and employee names",
        categories=["recon"], phases=["reconnaissance"], binaries=["theHarvester"],
        tags=["osint"], homepage="https://github.com/laramies/theHarvester",
        install=[apt("theharvester"), pipx("theHarvester")],
        alternatives=["spiderfoot", "amass"],
    ),
    dict(
        id="spiderfoot", summary="Automated OSINT collection and correlation platform",
        categories=["recon", "threat-intel"], phases=["reconnaissance"],
        binaries=["spiderfoot", "sf.py"], tags=["osint", "automation"],
        homepage="https://www.spiderfoot.net",
        install=[apt("spiderfoot"), pipx("spiderfoot")],
        alternatives=["theharvester", "maltego"],
    ),
    # ---- web --------------------------------------------------------------
    dict(
        id="ffuf", summary="Fast web fuzzer for content and parameter discovery",
        categories=["web", "fuzzing"], phases=["discovery"], binaries=["ffuf"],
        tags=["fuzzing", "bug-bounty"], homepage="https://github.com/ffuf/ffuf",
        license="Apache-2.0", verify="ffuf -V",
        install=[
            apt("ffuf"), brew("ffuf"),
            go("github.com/ffuf/ffuf/v2@latest"),
            gh("ffuf/ffuf"),
        ],
        alternatives=["feroxbuster", "gobuster", "dirsearch"],
    ),
    dict(
        id="feroxbuster", summary="Recursive content discovery written in Rust",
        categories=["web"], phases=["discovery"], binaries=["feroxbuster"],
        tags=["fuzzing", "bug-bounty"],
        homepage="https://github.com/epi052/feroxbuster",
        install=[
            apt("feroxbuster"), brew("feroxbuster"),
            cargo("feroxbuster"), gh("epi052/feroxbuster"),
        ],
        alternatives=["ffuf", "gobuster", "dirsearch"],
    ),
    dict(
        id="gobuster", summary="Directory, DNS and vhost brute-forcer",
        categories=["web"], phases=["discovery"], binaries=["gobuster"],
        tags=["fuzzing"], homepage="https://github.com/OJ/gobuster",
        install=[apt("gobuster"), brew("gobuster"), go("github.com/OJ/gobuster/v3@latest")],
        alternatives=["ffuf", "feroxbuster"],
    ),
    dict(
        id="nuclei", summary="Template-driven vulnerability scanner for known issues",
        categories=["vuln-scan", "web"], phases=["discovery"], binaries=["nuclei"],
        tags=["projectdiscovery", "bug-bounty", "templates"],
        homepage="https://github.com/projectdiscovery/nuclei", verify="nuclei -version",
        install=[
            apt("nuclei"), brew("nuclei"),
            go("github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest"),
            gh("projectdiscovery/nuclei"),
        ],
        alternatives=["nikto", "zaproxy"],
    ),
    dict(
        id="httpx", summary="Fast multi-purpose HTTP prober and fingerprinter",
        categories=["web", "recon"], phases=["discovery"], binaries=["httpx"],
        tags=["projectdiscovery", "bug-bounty"],
        homepage="https://github.com/projectdiscovery/httpx",
        install=[
            apt("httpx-toolkit"),
            go("github.com/projectdiscovery/httpx/cmd/httpx@latest"),
            gh("projectdiscovery/httpx"),
        ],
        alternatives=["httprobe", "whatweb"],
    ),
    dict(
        id="katana", summary="Crawling framework for mapping web application surface",
        categories=["web"], phases=["discovery"], binaries=["katana"],
        tags=["crawler", "projectdiscovery"],
        homepage="https://github.com/projectdiscovery/katana",
        install=[
            go("github.com/projectdiscovery/katana/cmd/katana@latest"),
            gh("projectdiscovery/katana"),
        ],
        alternatives=["gospider", "hakrawler"],
    ),
    dict(
        id="sqlmap", summary="Automated SQL injection detection and exploitation",
        categories=["web", "database"], phases=["initial-access", "credential-access"],
        binaries=["sqlmap"], tags=["sqli", "classic"], homepage="https://sqlmap.org",
        install=[apt("sqlmap"), brew("sqlmap"), pipx("sqlmap")],
        alternatives=["ghauri", "jsql-injection"],
    ),
    dict(
        id="nikto", summary="Web server misconfiguration and known-issue scanner",
        categories=["web", "vuln-scan"], phases=["discovery"], binaries=["nikto"],
        tags=["classic"], homepage="https://github.com/sullo/nikto",
        install=[apt("nikto"), brew("nikto")], alternatives=["nuclei", "zaproxy"],
    ),
    dict(
        id="zaproxy", summary="Full-featured web application scanner and intercepting proxy",
        categories=["web"], phases=["discovery"], binaries=["zaproxy", "zap.sh"],
        tags=["proxy", "owasp", "gui"], homepage="https://www.zaproxy.org",
        install=[apt("zaproxy"), brew("zap", cask=True)],
        alternatives=["burpsuite", "mitmproxy", "caido"],
    ),
    dict(
        id="burpsuite", summary="Intercepting proxy and web testing suite (community edition)",
        categories=["web"], phases=["discovery"], binaries=["burpsuite"],
        tags=["proxy", "gui"], homepage="https://portswigger.net/burp",
        install=[apt("burpsuite")], alternatives=["zaproxy", "caido", "mitmproxy"],
    ),
    dict(
        id="wpscan", summary="WordPress vulnerability and enumeration scanner",
        categories=["web", "vuln-scan"], phases=["discovery"], binaries=["wpscan"],
        tags=["cms"], homepage="https://wpscan.com",
        install=[apt("wpscan"), gem("wpscan")], alternatives=["nuclei"],
    ),
    dict(
        id="dalfox", summary="XSS scanner and parameter analysis tool",
        categories=["web"], phases=["discovery"], binaries=["dalfox"],
        tags=["xss", "bug-bounty"], homepage="https://github.com/hahwul/dalfox",
        install=[brew("dalfox"), go("github.com/hahwul/dalfox/v2@latest"), gh("hahwul/dalfox")],
        alternatives=["xsser"],
    ),
    dict(
        id="gowitness", summary="Web screenshot utility for triaging large host lists",
        categories=["web", "recon"], phases=["discovery"], binaries=["gowitness"],
        tags=["screenshots", "bug-bounty"],
        homepage="https://github.com/sensepost/gowitness",
        install=[apt("gowitness"), go("github.com/sensepost/gowitness@latest"),
                 gh("sensepost/gowitness")],
        alternatives=["aquatone", "eyewitness"],
    ),
    # ---- passwords --------------------------------------------------------
    dict(
        id="hashcat", summary="GPU-accelerated offline password recovery",
        categories=["password"], phases=["credential-access"], binaries=["hashcat"],
        tags=["cracking", "gpu"], homepage="https://hashcat.net/hashcat/",
        verify="hashcat --version",
        install=[apt("hashcat"), brew("hashcat")], alternatives=["john"],
    ),
    dict(
        id="john", summary="John the Ripper: offline password cracking, many formats",
        categories=["password"], phases=["credential-access"],
        binaries=["john", "unshadow", "zip2john"], tags=["cracking"],
        homepage="https://www.openwall.com/john/",
        install=[apt("john"), brew("john-jumbo")], alternatives=["hashcat"],
    ),
    dict(
        id="hydra", summary="Parallelised online login brute-forcer for many protocols",
        categories=["password"], phases=["credential-access"], binaries=["hydra"],
        tags=["online", "bruteforce"], homepage="https://github.com/vanhauser-thc/thc-hydra",
        install=[apt("hydra"), brew("hydra")], alternatives=["medusa", "ncrack", "patator"],
    ),
    dict(
        id="cewl", summary="Custom wordlist generator that spiders a target site",
        categories=["password"], phases=["credential-access"], binaries=["cewl"],
        tags=["wordlists"], homepage="https://github.com/digininja/CeWL",
        install=[apt("cewl")], alternatives=["crunch"],
    ),
    dict(
        id="seclists", summary="The collection of wordlists everything else expects",
        categories=["utility"], phases=["discovery"], binaries=[],
        tags=["wordlists", "data"], homepage="https://github.com/danielmiessler/SecLists",
        install=[apt("seclists"), brew("seclists")],
    ),
    # ---- exploitation / post-ex -------------------------------------------
    dict(
        id="metasploit-framework", summary="Exploit development and delivery framework",
        categories=["exploitation"], phases=["initial-access", "execution"],
        binaries=["msfconsole", "msfvenom", "msfdb"], tags=["framework", "classic"],
        homepage="https://www.metasploit.com", verify="msfconsole --version",
        install=[apt("metasploit-framework"), brew("metasploit")],
        alternatives=["sliver", "havoc"],
    ),
    dict(
        id="exploitdb", summary="Offline copy of Exploit-DB, searchable from the terminal",
        categories=["exploitation"], phases=["resource-development"],
        binaries=["searchsploit"], tags=["database", "classic"],
        homepage="https://www.exploit-db.com",
        install=[apt("exploitdb"), brew("exploitdb")],
    ),
    dict(
        id="impacket", summary="Python classes for working with Windows network protocols",
        categories=["post-exploitation"],
        phases=["lateral-movement", "credential-access"],
        binaries=["secretsdump.py", "psexec.py", "wmiexec.py", "GetUserSPNs.py"],
        tags=["windows", "active-directory", "smb"],
        homepage="https://github.com/fortra/impacket",
        install=[apt("impacket-scripts"), pipx("impacket")],
        alternatives=["netexec"],
    ),
    dict(
        id="netexec", summary="Network execution and enumeration across SMB, WinRM, LDAP",
        categories=["post-exploitation"], phases=["lateral-movement", "discovery"],
        binaries=["netexec", "nxc"], tags=["windows", "active-directory"],
        homepage="https://github.com/Pennyw0rth/NetExec",
        install=[apt("netexec"), pipx("netexec")],
        alternatives=["impacket", "crackmapexec"],
    ),
    dict(
        id="bloodhound", summary="Active Directory attack path mapping and analysis",
        categories=["post-exploitation"], phases=["discovery", "lateral-movement"],
        binaries=["bloodhound"], tags=["active-directory", "gui", "graph"],
        homepage="https://github.com/SpecterOps/BloodHound",
        install=[apt("bloodhound")], alternatives=["adalanche"],
    ),
    dict(
        id="evil-winrm", summary="WinRM shell for post-exploitation on Windows hosts",
        categories=["post-exploitation"], phases=["lateral-movement", "execution"],
        binaries=["evil-winrm"], tags=["windows"],
        homepage="https://github.com/Hackplayers/evil-winrm",
        install=[apt("evil-winrm"), gem("evil-winrm")],
    ),
    # ---- traffic ----------------------------------------------------------
    dict(
        id="wireshark", summary="Deep protocol analysis of captured network traffic",
        categories=["sniffing"], phases=["collection", "analysis"],
        binaries=["wireshark", "tshark"], tags=["pcap", "gui", "classic"],
        homepage="https://www.wireshark.org",
        install=[apt("wireshark"), brew("wireshark")], alternatives=["tcpdump", "termshark"],
    ),
    dict(
        id="tcpdump", summary="Command-line packet capture and filtering",
        categories=["sniffing"], phases=["collection"], binaries=["tcpdump"],
        tags=["pcap", "classic"], requires_root=True, homepage="https://www.tcpdump.org",
        install=[apt("tcpdump"), brew("tcpdump")], alternatives=["wireshark", "tshark"],
    ),
    dict(
        id="responder", summary="LLMNR, NBT-NS and MDNS poisoner for credential capture",
        categories=["sniffing", "post-exploitation"], phases=["credential-access"],
        binaries=["responder"], tags=["windows", "mitm"], requires_root=True,
        homepage="https://github.com/lgandx/Responder",
        install=[apt("responder")], alternatives=["pretender"],
    ),
    dict(
        id="mitmproxy", summary="Interactive TLS-capable intercepting proxy",
        categories=["sniffing", "web"], phases=["collection"],
        binaries=["mitmproxy", "mitmdump", "mitmweb"], tags=["mitm", "proxy"],
        homepage="https://mitmproxy.org",
        install=[apt("mitmproxy"), brew("mitmproxy"), pipx("mitmproxy")],
        alternatives=["burpsuite", "zaproxy"],
    ),
    dict(
        id="bettercap", summary="Network attack and monitoring framework",
        categories=["sniffing", "wireless"], phases=["collection", "credential-access"],
        binaries=["bettercap"], tags=["mitm"], requires_root=True,
        homepage="https://www.bettercap.org",
        install=[apt("bettercap"), brew("bettercap")], alternatives=["ettercap"],
    ),
    # ---- wireless ---------------------------------------------------------
    dict(
        id="aircrack-ng", summary="802.11 capture, injection and WPA handshake cracking",
        categories=["wireless"], phases=["credential-access", "collection"],
        binaries=["aircrack-ng", "airodump-ng", "aireplay-ng", "airmon-ng"],
        tags=["wifi", "classic"], requires_root=True, homepage="https://www.aircrack-ng.org",
        install=[apt("aircrack-ng"), brew("aircrack-ng")], alternatives=["wifite", "hcxdumptool"],
    ),
    dict(
        id="wifite", summary="Automated wireless auditing wrapper around the aircrack suite",
        categories=["wireless"], phases=["credential-access"], binaries=["wifite"],
        tags=["wifi", "automation"], requires_root=True,
        homepage="https://github.com/kimocoder/wifite2",
        install=[apt("wifite")], alternatives=["aircrack-ng"],
    ),
    dict(
        id="kismet", summary="Wireless network detector, sniffer and IDS",
        categories=["wireless", "monitoring"], phases=["discovery", "collection"],
        binaries=["kismet"], tags=["wifi", "ids"], homepage="https://www.kismetwireless.net",
        install=[apt("kismet")],
    ),
    # ---- reverse engineering ---------------------------------------------
    dict(
        id="ghidra", summary="NSA's software reverse engineering suite with a decompiler",
        categories=["reverse"], phases=["analysis"], binaries=["ghidraRun"],
        tags=["decompiler", "gui"], homepage="https://ghidra-sre.org",
        install=[apt("ghidra"), brew("ghidra", cask=True)],
        alternatives=["radare2", "cutter", "ida-free"],
    ),
    dict(
        id="radare2", summary="Command-line reverse engineering and binary analysis framework",
        categories=["reverse"], phases=["analysis"], binaries=["r2", "radare2", "rabin2"],
        tags=["disassembler"], homepage="https://rada.re",
        install=[apt("radare2"), brew("radare2")], alternatives=["rizin", "ghidra"],
    ),
    dict(
        id="apktool", summary="Decode and rebuild Android APK resources",
        categories=["reverse", "mobile"], phases=["analysis"], binaries=["apktool"],
        tags=["android"], homepage="https://apktool.org",
        install=[apt("apktool"), brew("apktool")], alternatives=["jadx"],
    ),
    dict(
        id="binwalk", summary="Firmware image analysis and extraction",
        categories=["reverse", "forensics"], phases=["analysis"], binaries=["binwalk"],
        tags=["firmware", "carving"], homepage="https://github.com/ReFirmLabs/binwalk",
        install=[apt("binwalk"), brew("binwalk")],
    ),
    # ---- forensics / DFIR -------------------------------------------------
    dict(
        id="sleuthkit", summary="Filesystem and disk image forensic analysis toolkit",
        categories=["forensics"], phases=["analysis"],
        binaries=["fls", "mmls", "icat", "tsk_recover"], tags=["disk", "classic"],
        homepage="https://www.sleuthkit.org",
        install=[apt("sleuthkit"), brew("sleuthkit")], alternatives=["autopsy"],
    ),
    dict(
        id="autopsy", summary="Graphical front-end for disk forensics and timeline analysis",
        categories=["forensics"], phases=["analysis"], binaries=["autopsy"],
        tags=["disk", "gui"], homepage="https://www.autopsy.com",
        install=[apt("autopsy")], alternatives=["sleuthkit"],
    ),
    dict(
        id="volatility3", summary="Memory forensics framework for RAM images",
        categories=["forensics", "malware"], phases=["analysis"],
        binaries=["vol", "volatility3"], tags=["memory"],
        homepage="https://github.com/volatilityfoundation/volatility3",
        install=[apt("volatility3"), pipx("volatility3")],
    ),
    dict(
        id="chainsaw", summary="Fast Windows event log hunting with Sigma rule support",
        categories=["detection", "incident-response"], phases=["analysis"],
        binaries=["chainsaw"], tags=["windows", "evtx", "sigma", "blue-team"],
        homepage="https://github.com/WithSecureLabs/chainsaw",
        install=[apt("chainsaw"), gh("WithSecureLabs/chainsaw")],
        alternatives=["hayabusa", "zircolite"],
    ),
    dict(
        id="hayabusa", summary="Windows event log timeline generator and threat hunter",
        categories=["detection", "incident-response"], phases=["analysis"],
        binaries=["hayabusa"], tags=["windows", "evtx", "sigma", "blue-team"],
        homepage="https://github.com/Yamato-Security/hayabusa",
        install=[gh("Yamato-Security/hayabusa")], alternatives=["chainsaw"],
    ),
    dict(
        id="velociraptor", summary="Endpoint visibility and digital forensics at scale",
        categories=["incident-response", "detection"], phases=["collection", "analysis"],
        binaries=["velociraptor"], tags=["edr", "dfir", "blue-team"],
        homepage="https://docs.velociraptor.app",
        install=[gh("Velocidex/velociraptor")], alternatives=["osquery"],
    ),
    dict(
        id="yara", summary="Pattern matching engine for classifying malware samples",
        categories=["malware", "detection"], phases=["analysis"], binaries=["yara", "yarac"],
        tags=["signatures", "blue-team"], homepage="https://virustotal.github.io/yara/",
        install=[apt("yara"), brew("yara")],
    ),
    dict(
        id="zeek", summary="Network security monitor producing rich protocol logs",
        categories=["monitoring", "detection"], phases=["collection"],
        binaries=["zeek", "zeek-cut"], tags=["nsm", "blue-team"], homepage="https://zeek.org",
        install=[apt("zeek"), brew("zeek")], alternatives=["suricata"],
    ),
    dict(
        id="suricata", summary="High-performance IDS, IPS and network security monitor",
        categories=["monitoring", "detection"], phases=["collection"], binaries=["suricata"],
        tags=["ids", "blue-team"], homepage="https://suricata.io",
        install=[apt("suricata"), brew("suricata")], alternatives=["zeek", "snort"],
    ),
    dict(
        id="sigma-cli", summary="Convert Sigma detection rules to your SIEM's query language",
        categories=["detection"], phases=["analysis", "reporting"], binaries=["sigma"],
        tags=["detection-engineering", "blue-team"],
        homepage="https://github.com/SigmaHQ/sigma-cli",
        install=[pipx("sigma-cli")],
    ),
    # ---- cloud / containers ----------------------------------------------
    dict(
        id="trivy", summary="Vulnerability and misconfiguration scanner for images and IaC",
        categories=["cloud", "vuln-scan"], phases=["discovery"], binaries=["trivy"],
        tags=["containers", "sca", "blue-team"], homepage="https://trivy.dev",
        install=[apt("trivy"), brew("trivy"), gh("aquasecurity/trivy")],
        alternatives=["grype", "syft"],
    ),
    dict(
        id="prowler", summary="Cloud security posture assessment for AWS, Azure and GCP",
        categories=["cloud"], phases=["discovery"], binaries=["prowler"],
        tags=["aws", "azure", "gcp", "compliance"], homepage="https://prowler.com",
        install=[pipx("prowler"), brew("prowler")], alternatives=["scoutsuite"],
    ),
    dict(
        id="scoutsuite", summary="Multi-cloud security auditing and reporting",
        categories=["cloud"], phases=["discovery"], binaries=["scout"],
        tags=["aws", "azure", "gcp"], homepage="https://github.com/nccgroup/ScoutSuite",
        install=[apt("scoutsuite"), pipx("scoutsuite")], alternatives=["prowler"],
    ),
]


def main() -> int:
    out_root = REPO_ROOT / "catalog"
    written = 0
    for entry in CURATED:
        payload = dict(entry)
        payload["install"] = [dict(m) for m in payload.get("install", [])]
        tool = Tool.from_dict(payload)
        # Confirm every method round-trips through the provider spec validator.
        for method in tool.install:
            assert isinstance(method, InstallMethod)
        dump_tool(tool, out_root / tool.category / f"{tool.id}.yaml")
        written += 1
    print(f"wrote {written} curated entries to {out_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
