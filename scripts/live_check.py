"""Small manual live check. Start FastAPI first; this contacts the running app."""

import json

import httpx


BASE_URL = "http://127.0.0.1:8000"


def search(client: httpx.Client, start: str, destination: str) -> dict:
    response = client.post("/api/searches", json={"start": start, "destination": destination})
    response.raise_for_status()
    result = response.json()
    print(f"Searching {start} -> {destination}", flush=True)
    with client.stream("GET", f"/api/searches/{result['id']}/events") as stream:
        stream.raise_for_status()
        for line in stream.iter_lines():
            if not line.startswith("data: "):
                continue
            data = json.loads(line[6:])
            if "status" in data:
                result = data
                if result["status"] != "running":
                    break
            elif "article" in data:
                print(f"  Expanded {data['article']['title']} ({data['stats']['network_requests']} requests)", flush=True)
    print(json.dumps({"status": result["status"], "click_count": result["click_count"], "route": [edge["source"]["title"] for edge in result["route"]] + ([result["destination"]["title"]] if result["status"] == "path_found" else []), "stats": result["stats"], "message": result["message"]}), flush=True)
    return result


def main() -> None:
    with httpx.Client(base_url=BASE_URL, timeout=90) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/").status_code == 200
        same = search(client, "Domestic cat", "Cat")
        assert same["status"] == "path_found" and same["click_count"] == 0
        search(client, "Ailurophobia", "Mammal")
        invalid = client.post("/api/searches", json={"start": "https://example.com/wiki/Cat", "destination": "Cat"})
        assert invalid.status_code == 422
        response = client.post("/api/searches", json={"start": "Cat", "destination": "Quantum mechanics"})
        response.raise_for_status()
        search_id = response.json()["id"]
        busy = client.post("/api/searches", json={"start": "Cat", "destination": "Mammal"})
        assert busy.status_code == 409
        cancelled = client.post(f"/api/searches/{search_id}/cancel").json()
        assert cancelled["status"] == "cancelled"
        print("Health, frontend serving, redirects, SSE, invalid inputs, busy handling, and cancellation verified.", flush=True)


if __name__ == "__main__":
    main()
