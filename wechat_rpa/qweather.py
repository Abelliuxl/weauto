from __future__ import annotations

import base64
from datetime import date as date_cls
from datetime import timedelta
import gzip
import json
from pathlib import Path
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


class QWeatherError(RuntimeError):
    pass


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _json_b64url(data: dict) -> str:
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return _b64url(raw)


def build_qweather_jwt(
    *,
    key_id: str,
    project_id: str,
    private_key_path: str,
    ttl_sec: int = 900,
) -> str:
    kid = str(key_id or "").strip()
    sub = str(project_id or "").strip()
    key_path = Path(str(private_key_path or "")).expanduser()
    if not kid:
        raise QWeatherError("missing qweather_jwt_key_id")
    if not sub:
        raise QWeatherError("missing qweather_jwt_project_id")
    if not key_path.is_file():
        raise QWeatherError(f"missing qweather private key: {key_path}")

    now = int(time.time()) - 30
    ttl = max(60, min(86400, int(ttl_sec or 900)))
    header = _json_b64url({"alg": "EdDSA", "kid": kid})
    payload = _json_b64url({"sub": sub, "iat": now, "exp": now + ttl})
    signing_input = f"{header}.{payload}"

    with tempfile.NamedTemporaryFile("wb", delete=True) as fp:
        fp.write(signing_input.encode("ascii"))
        fp.flush()
        proc = subprocess.run(
            [
                "openssl",
                "pkeyutl",
                "-sign",
                "-inkey",
                str(key_path),
                "-rawin",
                "-in",
                fp.name,
            ],
            check=False,
            capture_output=True,
        )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        raise QWeatherError(f"qweather jwt signing failed: {detail or proc.returncode}")
    return f"{signing_input}.{_b64url(proc.stdout)}"


class QWeatherClient:
    def __init__(
        self,
        *,
        api_host: str,
        auth_type: str = "jwt",
        api_key: str = "",
        jwt_key_id: str = "",
        jwt_project_id: str = "",
        jwt_private_key_path: str = "",
        jwt_ttl_sec: int = 900,
        timeout_sec: float = 8.0,
    ) -> None:
        host = str(api_host or "").strip().rstrip("/")
        if host and not host.startswith(("http://", "https://")):
            host = f"https://{host}"
        self.api_host = host
        self.auth_type = str(auth_type or "jwt").strip().lower()
        self.api_key = str(api_key or "").strip()
        self.jwt_key_id = str(jwt_key_id or "").strip()
        self.jwt_project_id = str(jwt_project_id or "").strip()
        self.jwt_private_key_path = str(jwt_private_key_path or "").strip()
        self.jwt_ttl_sec = int(jwt_ttl_sec or 900)
        self.timeout_sec = max(1.0, float(timeout_sec or 8.0))

    def is_configured(self) -> bool:
        if not self.api_host:
            return False
        if self.auth_type == "api_key":
            return bool(self.api_key)
        return bool(
            self.jwt_key_id
            and self.jwt_project_id
            and self.jwt_private_key_path
            and Path(self.jwt_private_key_path).expanduser().is_file()
        )

    def status_text(self) -> str:
        if not self.api_host:
            return "blocked (tool=query_weather missing qweather_api_host)"
        if self.auth_type == "api_key":
            if self.api_key:
                return f"available (tool=query_weather provider=qweather auth=api_key host={self.api_host})"
            return "blocked (tool=query_weather missing qweather_api_key/env)"
        missing = []
        if not self.jwt_key_id:
            missing.append("qweather_jwt_key_id")
        if not self.jwt_project_id:
            missing.append("qweather_jwt_project_id")
        if not self.jwt_private_key_path:
            missing.append("qweather_jwt_private_key_path")
        elif not Path(self.jwt_private_key_path).expanduser().is_file():
            missing.append(f"private key file {self.jwt_private_key_path}")
        if missing:
            return f"blocked (tool=query_weather missing {', '.join(missing)})"
        return f"available (tool=query_weather provider=qweather auth=jwt host={self.api_host})"

    def query(
        self,
        *,
        location: str,
        days: int = 3,
        mode: str = "both",
        date: str = "",
        day_offset: int | None = None,
    ) -> str:
        if not self.is_configured():
            raise QWeatherError(self.status_text())
        clean_location = str(location or "").strip()
        if not clean_location:
            raise QWeatherError("missing location")
        target_date = self._normalize_target_date(date=date, day_offset=day_offset)
        forecast_days = max(1, min(30, int(days or 3)))
        if target_date is not None:
            delta_days = (target_date - date_cls.today()).days
            if delta_days < 0:
                raise QWeatherError(f"target date is in the past: {target_date.isoformat()}")
            if delta_days > 29:
                raise QWeatherError(f"target date is beyond 30-day forecast: {target_date.isoformat()}")
            forecast_days = max(forecast_days, delta_days + 1)
        clean_mode = str(mode or "both").strip().lower()
        if clean_mode not in {"now", "forecast", "both"}:
            clean_mode = "both"

        place = self._lookup_location(clean_location)
        location_id = str(place.get("id", "")).strip()
        if not location_id:
            raise QWeatherError(f"location not found: {clean_location}")

        parts = [self._format_location(place)]
        if clean_mode in {"now", "both"}:
            parts.append(self._format_now(self._request_json("/v7/weather/now", {"location": location_id})))
        if clean_mode in {"forecast", "both"}:
            endpoint_days = self._forecast_endpoint_days(forecast_days)
            parts.append(
                self._format_forecast(
                    self._request_json(f"/v7/weather/{endpoint_days}d", {"location": location_id}),
                    days=forecast_days,
                    target_date=target_date,
                )
            )
        return "\n".join(part for part in parts if part).strip()

    @staticmethod
    def _forecast_endpoint_days(days: int) -> int:
        clean_days = max(1, min(30, int(days or 3)))
        for candidate in (3, 7, 10, 15, 30):
            if clean_days <= candidate:
                return candidate
        return 30

    @staticmethod
    def _normalize_target_date(*, date: str = "", day_offset: int | None = None) -> date_cls | None:
        clean_date = str(date or "").strip()
        if clean_date:
            try:
                return date_cls.fromisoformat(clean_date)
            except Exception as exc:
                raise QWeatherError(f"invalid date, expected YYYY-MM-DD: {clean_date}") from exc
        if day_offset is None:
            return None
        try:
            offset = int(day_offset)
        except Exception as exc:
            raise QWeatherError(f"invalid day_offset: {day_offset}") from exc
        return date_cls.today() + timedelta(days=offset)

    def _auth_headers(self) -> dict[str, str]:
        if self.auth_type == "api_key":
            return {"X-QW-Api-Key": self.api_key}
        token = build_qweather_jwt(
            key_id=self.jwt_key_id,
            project_id=self.jwt_project_id,
            private_key_path=self.jwt_private_key_path,
            ttl_sec=self.jwt_ttl_sec,
        )
        return {"Authorization": f"Bearer {token}"}

    def _request_json(self, path: str, params: dict[str, object]) -> dict:
        if not self.api_host:
            raise QWeatherError("missing qweather_api_host")
        clean_path = "/" + str(path or "").lstrip("/")
        query = urllib.parse.urlencode({k: v for k, v in params.items() if str(v) != ""})
        url = f"{self.api_host}{clean_path}"
        if query:
            url += f"?{query}"
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "User-Agent": "weauto/0.1",
            **self._auth_headers(),
        }
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(
                req,
                timeout=self.timeout_sec,
                context=ssl.create_default_context(),
            ) as resp:
                body = resp.read()
                if str(resp.headers.get("Content-Encoding", "")).lower() == "gzip":
                    body = gzip.decompress(body)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            raise QWeatherError(f"qweather http {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise QWeatherError(f"qweather request failed: {exc}") from exc
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception as exc:
            raise QWeatherError("qweather returned invalid json") from exc
        if not isinstance(data, dict):
            raise QWeatherError("qweather returned non-object json")
        code = str(data.get("code", "")).strip()
        if code and code != "200":
            raise QWeatherError(f"qweather api code={code}: {json.dumps(data, ensure_ascii=False)[:400]}")
        return data

    def _lookup_location(self, location: str) -> dict:
        data = self._request_json("/geo/v2/city/lookup", {"location": location, "number": 1})
        rows = data.get("location")
        if not isinstance(rows, list) or not rows:
            raise QWeatherError(f"location not found: {location}")
        first = rows[0]
        if not isinstance(first, dict):
            raise QWeatherError(f"location not found: {location}")
        return first

    @staticmethod
    def _format_location(place: dict) -> str:
        name = str(place.get("name", "")).strip()
        adm2 = str(place.get("adm2", "")).strip()
        adm1 = str(place.get("adm1", "")).strip()
        country = str(place.get("country", "")).strip()
        chunks = [x for x in (name, adm2, adm1, country) if x]
        return f"天气查询地点: {'/'.join(chunks)}"

    @staticmethod
    def _format_now(data: dict) -> str:
        now = data.get("now") if isinstance(data.get("now"), dict) else {}
        obs_time = str(now.get("obsTime", "")).strip()
        text = str(now.get("text", "")).strip()
        temp = str(now.get("temp", "")).strip()
        feels = str(now.get("feelsLike", "")).strip()
        wind_dir = str(now.get("windDir", "")).strip()
        wind_scale = str(now.get("windScale", "")).strip()
        humidity = str(now.get("humidity", "")).strip()
        precip = str(now.get("precip", "")).strip()
        pieces = []
        if text or temp:
            pieces.append(f"现在: {text or '-'} {temp or '-'}C")
        if feels:
            pieces.append(f"体感 {feels}C")
        if wind_dir or wind_scale:
            pieces.append(f"{wind_dir or ''}{wind_scale + '级' if wind_scale else ''}")
        if humidity:
            pieces.append(f"湿度 {humidity}%")
        if precip:
            pieces.append(f"降水 {precip}mm")
        if obs_time:
            pieces.append(f"观测 {obs_time}")
        return "；".join(pieces)

    @staticmethod
    def _format_forecast(data: dict, *, days: int, target_date: date_cls | None = None) -> str:
        rows = data.get("daily")
        if not isinstance(rows, list) or not rows:
            return ""
        target_text = target_date.isoformat() if target_date is not None else ""
        if target_text:
            rows = [
                item
                for item in rows
                if isinstance(item, dict) and str(item.get("fxDate", "")).strip() == target_text
            ]
            lines = [f"预报[{target_text}]:"]
            if not rows:
                return f"预报[{target_text}]: 未返回目标日期"
        else:
            lines = ["预报:"]
        for item in rows[: max(1, min(30, days))]:
            if not isinstance(item, dict):
                continue
            date = str(item.get("fxDate", "")).strip()
            day = str(item.get("textDay", "")).strip()
            night = str(item.get("textNight", "")).strip()
            temp_min = str(item.get("tempMin", "")).strip()
            temp_max = str(item.get("tempMax", "")).strip()
            wind = str(item.get("windDirDay", "")).strip()
            wind_scale = str(item.get("windScaleDay", "")).strip()
            pop = str(item.get("precip", "") or item.get("precipitation", "")).strip()
            weather = day if day == night or not night else f"{day}转{night}"
            line = f"- {date}: {weather} {temp_min}-{temp_max}C"
            if wind or wind_scale:
                line += f"，{wind}{wind_scale + '级' if wind_scale else ''}"
            if pop:
                line += f"，降水 {pop}mm"
            lines.append(line)
        return "\n".join(lines)
