"""Isolated native ClickHouse-local storage. Cannot connect to a remote server."""
import fcntl
import gzip
import json
from pathlib import Path
import subprocess
import time
from uuid import uuid4

from sales_coach.domain.sales_v2 import normalize, canonical, METRICS, QUANTITIES, number
from sales_coach.services.sales_v2_capture import atomic_json, now


class LocalSalesV2Repository:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.binary = self.root/"bin"/"clickhouse"
        self.storage = self.root/"clickhouse-data"
        if not self.binary.is_file() or not (self.root/"preflight.json").is_file():
            raise ValueError("Explicit isolated pilot directory and binary required")
        self.storage.mkdir(exist_ok=True)

    def query(self, sql):
        # No host, credentials, client or production configuration is accepted.
        with (self.root/"native.lock").open("a") as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            result = subprocess.run([str(self.binary), "local", "--path", str(self.storage),
                "--max_threads", "2", "--query", sql], check=True, text=True, capture_output=True)
        return result.stdout

    def initialize(self):
        columns = ["load_id String", "capture_id String", "date Date", "page UInt32", "ordinal UInt32",
                   "captured_at String", "normalizer_version String", "raw_hash FixedString(64)",
                   "commercial_included UInt8", "exclusion Nullable(String)",
                   "idDeposito Nullable(Int64)", "dsDeposito Nullable(String)", "planillaCarga Nullable(String)",
                   "route_description Nullable(String)", "document_identity Nullable(String)", "line_identity Nullable(String)"]
        columns += [f"{k} Nullable(String)" for k in (
            "idEmpresa","dsEmpresa","idSucursal","idArticulo","idArticuloEstadistico","dsArticulo",
            "idVendedor","dsVendedor","idFuerzaVentas","dsFuerzaVentas","idCanalMkt","dsCanalMkt",
            "idSubcanalMkt","dsSubcanalMKT","idCliente","idDocumento","letra","serie","nrodoc","idLinea","rowVersion")]
        columns += [f"{k} Nullable(Decimal(38,12))" for k in QUANTITIES]
        columns += [f"{k} Float64" for k in METRICS]
        columns += ["legacy_json String CODEC(ZSTD)", "raw_json String CODEC(ZSTD)"]
        self.query("CREATE TABLE IF NOT EXISTS fact_sales_lines_v2 ("+",".join(columns)+") ENGINE=MergeTree PARTITION BY toYYYYMM(date) ORDER BY (date,load_id,page,ordinal)")
        # Expose source money independently from the deliberately unchanged legacy metrics.
        for field in ("subtotalNeto","subtotalFinal","subtotalBruto","internos","iva21","iva105","iva27"):
            self.query(f"ALTER TABLE fact_sales_lines_v2 ADD COLUMN IF NOT EXISTS {field} Nullable(Decimal(38,12)) ALIAS toDecimal128OrNull(JSONExtractRaw(raw_json,'{field}'),12)")
        self.query("CREATE TABLE IF NOT EXISTS sales_capture_pages_v2 (load_id String,capture_id String,date Date,pass_number UInt8,page UInt32,sha256 FixedString(64),metadata String CODEC(ZSTD),payload String CODEC(ZSTD)) ENGINE=MergeTree ORDER BY (date,load_id,pass_number,page)")

    def insert_file(self, table, path):
        if table not in ("fact_sales_lines_v2", "sales_capture_pages_v2"):
            raise ValueError("Pilot table not allowed")
        path = Path(path).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Input must be inside isolated pilot")
        quoted = str(path).replace("\\", "\\\\").replace("'", "\\'")
        self.query(f"INSERT INTO {table} FROM INFILE '{quoted}' FORMAT JSONEachRow")

    def load_day(self, entry):
        if entry["status"] != "source_verified":
            raise ValueError("Partial source attempt cannot be selected")
        load_id = uuid4().hex
        day = entry["day"]
        files = self.root/"staging"/load_id
        files.mkdir(parents=True)
        linefile, pagefile = files/"lines.jsonl", files/"pages.jsonl"
        tick = time.monotonic()
        included, excluded, rows = 0, 0, 0
        with linefile.open("w") as lines, pagefile.open("w") as pages:
            for pass_number, summary in enumerate(entry["passes"],1):
                for p in summary["pages"]:
                    rawpath = self.root/p["path"]
                    payload = gzip.open(rawpath,"rb").read()
                    import hashlib
                    if hashlib.sha256(payload).hexdigest() != p["sha256"]:
                        raise ValueError("Archived response checksum mismatch")
                    pages.write(canonical({"load_id":load_id,"capture_id":entry["capture_id"],"date":day,
                        "pass_number":pass_number,"page":p["page"],"sha256":p["sha256"],"metadata":canonical(p),"payload":payload.decode()})+"\n")
                    if pass_number != 1:
                        continue
                    for ordinal, raw in enumerate(json.loads(payload)["dsReporteComprobantesApi"].get("VentasResumen",[])):
                        row = normalize(raw,capture_id=entry["capture_id"],page=p["page"],ordinal=ordinal,captured_at=p["finished_at"])
                        item = {k:row[k] for k in ("capture_id","date","page","ordinal","captured_at","normalizer_version","raw_hash","commercial_included","exclusion","idDeposito","dsDeposito","planillaCarga","route_description","document_identity","line_identity")}
                        item["load_id"] = load_id
                        for k in ("idEmpresa","dsEmpresa","idSucursal","idArticulo","idArticuloEstadistico","dsArticulo",
                            "idVendedor","dsVendedor","idFuerzaVentas","dsFuerzaVentas","idCanalMkt","dsCanalMkt",
                            "idSubcanalMkt","dsSubcanalMKT","idCliente","idDocumento","letra","serie","nrodoc","idLinea","rowVersion"):
                            item[k] = str(raw[k]) if raw.get(k) is not None else None
                        for k in QUANTITIES:
                            n=number(raw.get(k)); item[k]=str(n) if n is not None else None
                            if n is not None and (abs(n)>=10**26 or n.as_tuple().exponent < -12):
                                raise ValueError("Decimal out of range: original retained, conversion blocked")
                        legacy = row["legacy"] or {}
                        item.update({m:legacy.get(m,0) for m in METRICS})
                        item.update(legacy_json=canonical(row["legacy"]),raw_json=canonical(raw))
                        lines.write(canonical(item)+"\n")
                        rows+=1; included+=bool(row["commercial_included"]); excluded+=not row["commercial_included"]
        if rows:
            self.insert_file("fact_sales_lines_v2",linefile)
        self.insert_file("sales_capture_pages_v2",pagefile)
        result=json.loads(self.query(f"SELECT count() n,uniqExact(tuple(page,ordinal)) identities FROM fact_sales_lines_v2 WHERE load_id='{load_id}' FORMAT JSONEachRow"))
        if int(result["n"]) != rows or int(result["identities"]) != rows:
            raise ValueError("Uncertain or duplicated insert; attempt remains invisible")
        # Verify every preserved row and its canonical digest after roundtrip.
        from sales_coach.domain.sales_v2 import digest, multiset_hash
        roundtrip=self.query(f"SELECT raw_hash,raw_json,idDeposito,dsDeposito,planillaCarga,route_description,idEmpresa,idArticulo,idArticuloEstadistico FROM fact_sales_lines_v2 WHERE load_id='{load_id}' FORMAT JSONEachRow")
        restored=[]
        for text in roundtrip.splitlines():
            r=json.loads(text);raw=json.loads(r["raw_json"])
            if digest(raw)!=r["raw_hash"] or raw.get("planillaCarga")!=r["planillaCarga"]:
                raise ValueError("Raw roundtrip failed")
            from sales_coach.domain.sales_v2 import identifier
            if identifier(raw.get("idDeposito")) != identifier(r["idDeposito"]):
                raise ValueError("Deposit changed during storage")
            if raw.get("dsDeposito") != r["dsDeposito"] or raw.get("desRuta") != r["route_description"]:
                raise ValueError("Deposit name or commercial route changed")
            for key in ("idEmpresa","idArticulo","idArticuloEstadistico"):
                if (str(raw[key]) if raw.get(key) is not None else None) != r[key]:
                    raise ValueError("Source identity changed during storage")
            restored.append(raw)
        if multiset_hash(restored)!=entry["passes"][0]["digest"]:
            raise ValueError("Source and V2 multiset mismatch")
        stored={"day":day,"capture_id":entry["capture_id"],"load_id":load_id,"rows":rows,"included":included,
            "excluded":excluded,"raw_roundtrip_verified":True,"source_digest":entry["passes"][0]["digest"],
            "seconds":time.monotonic()-tick,"finished_at":now(),"status":"candidate_only"}
        atomic_json(self.root/"loads"/(load_id+".json"),stored)
        atomic_json(self.root/"selected"/(day+".json"),stored)
        # These are derived temporary inputs, not raw evidence. Keep until final audit.
        return stored

    def load_available(self):
        lock=(self.root/"storage.lock").open("w")
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            self.initialize()
            for file in sorted((self.root/"days").glob("*.json")):
                entry=json.loads(file.read_text());selected=self.root/"selected"/file.name
                if entry["status"]!="source_verified":continue
                if selected.exists() and json.loads(selected.read_text())["capture_id"]==entry["capture_id"]:continue
                print(canonical(self.load_day(entry)),flush=True)
        finally:lock.close()
