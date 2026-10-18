"""Toy rollback bypasses both mitmproxy and inherited internet proxies."""
import httpx


def rollback_last(plan, entry, flow_summary):
    with httpx.Client(trust_env=False, timeout=5) as client:
        response = client.post(flow_summary["origin"] + "/admin/rollback-last")
        response.raise_for_status()
