from __future__ import annotations

import base64
import re
from dataclasses import dataclass

from .errors import DmsError


@dataclass
class Raw:
    """A binary response (downloads, exports). Always served as an attachment."""
    data: bytes
    filename: str
    ctype: str = "application/octet-stream"


def b64(v) -> bytes:
    try:
        return base64.b64decode(str(v or ""), validate=True)
    except Exception:
        raise DmsError(400, "content_b64 가 올바르지 않습니다")


def build_routes(app):
    """(method, regex, fn(req, user, match, qs), mutating). All business rules live in Dms; this only maps HTTP to calls."""
    d = app.dms
    R: list = []

    def route(method, pattern, mutating=False):
        def deco(fn):
            R.append((method, re.compile(pattern), fn, mutating or method != "GET"))
            return fn
        return deco

    q1 = lambda qs, k, default=None: (qs.get(k) or [default])[0]  # noqa: E731
    big = lambda: int(d.max_upload * 1.4) + (1 << 20)             # noqa: E731  base64 overhead + metadata
    i = lambda m, n=1: int(m.group(n))                            # noqa: E731

    # ---- config / dashboard ----
    @route("GET", r"/api/dms/config")
    def _config(req, u, m, qs):
        return {"doc_types": d.list_doc_types(u), "statuses": ["draft", "in_review", "approved", "rejected", "expired"],
                "share": {"enabled": d.share_cfg["enabled"], "max_days": d.share_cfg["max_days"], "max_level": d.share_cfg["max_level"]},
                "max_upload_mb": d.max_upload // 1048576, "trash_days": d.trash_days,
                "users": [{"id": x.id, "name": x.name, "dept": x.dept} for x in app.users.values()]}

    @route("GET", r"/api/dms/dashboard")
    def _dash(req, u, m, qs):
        return d.dashboard(u)

    @route("GET", r"/api/dms/templates")
    def _templates(req, u, m, qs):
        return {"templates": d.templates(u)}

    @route("POST", r"/api/dms/templates/render")
    def _tpl_render(req, u, m, qs):
        b = req.body(65536)
        return d.render_template(u, b.get("template_id"), b.get("values") or {}, b.get("title", ""))

    @route("GET", r"/api/docs/(\d+)/text")
    def _text_get(req, u, m, qs):
        return d.read_text(u, i(m))

    @route("PUT", r"/api/docs/(\d+)/text")
    def _text_put(req, u, m, qs):
        b = req.body(1_300_000)
        return d.edit_text(u, i(m), b.get("text"), b.get("comment", ""))

    @route("POST", r"/api/dms/access-requests")
    def _ar_new(req, u, m, qs):
        b = req.body(8192)
        return d.request_access(u, b.get("ref"), b.get("role", "viewer"), b.get("reason", ""))

    @route("GET", r"/api/dms/access-requests")
    def _ar_list(req, u, m, qs):
        return d.list_access_requests(u)

    @route("POST", r"/api/dms/access-requests/(\d+)/decide")
    def _ar_decide(req, u, m, qs):
        b = req.body(8192)
        return d.decide_access(u, i(m), bool(b.get("approve")), b.get("note", ""))

    @route("POST", r"/api/docs/(\d+)/ask")
    def _ask_doc(req, u, m, qs):
        return d.ask_doc(u, i(m), req.body(8192).get("question"))

    @route("POST", r"/api/docs/(\d+)/follow")
    def _follow(req, u, m, qs):
        return d.set_follow(u, i(m), bool(req.body(1024).get("on")))

    @route("GET", r"/api/dms/follows")
    def _follows(req, u, m, qs):
        return {"items": d.my_follows(u)}

    @route("GET", r"/api/dms/activity")
    def _activity(req, u, m, qs):
        return {"items": d.activity(u, min(int(q1(qs, "limit", 40)), 100))}

    @route("GET", r"/api/dms/calendar")
    def _calendar(req, u, m, qs):
        return d.calendar(u, q1(qs, "month"))

    @route("GET", r"/api/alerts")
    def _alerts(req, u, m, qs):
        return d.alerts(u)

    # ---- folders ----
    @route("GET", r"/api/dms/folders")
    def _folders(req, u, m, qs):
        return d.folder_tree(u)

    @route("POST", r"/api/dms/folders")
    def _mkfolder(req, u, m, qs):
        b = req.body(65536)
        return d.create_folder(u, b.get("parent_id"), b.get("name"), b.get("entries"))

    @route("PUT", r"/api/dms/folders/(\d+)")
    def _editfolder(req, u, m, qs):
        b, fid = req.body(65536), i(m)
        out = None
        if "name" in b:
            out = d.rename_folder(u, fid, b["name"])
        if "parent_id" in b:
            out = d.move_folder(u, fid, b["parent_id"])
        if "entries" in b or "inherit" in b:
            out = d.set_folder_acl(u, fid, b.get("entries", d.folder(fid)["entries"] if d.folder(fid) else []), b.get("inherit"))
        return out or d.folder(fid)

    @route("DELETE", r"/api/dms/folders/(\d+)")
    def _rmfolder(req, u, m, qs):
        return d.delete_folder(u, i(m))

    # ---- documents ----
    @route("GET", r"/api/docs")
    def _search(req, u, m, qs):
        g = lambda k: q1(qs, k)  # noqa: E731
        return d.search(u, g("q"), type=g("type"), status=g("status"), tag=g("tag"), folder=g("folder"), owner=g("owner"), origin=g("origin"),
                        date_from=g("from"), date_to=g("to"), mine=g("mine") == "1", favorites=g("favorites") == "1", sort=g("sort"),
                        page=g("page") or 1, size=g("size") or 30)

    @route("POST", r"/api/docs")
    def _register(req, u, m, qs):
        b = req.body(big())
        return d.register(u, b.get("filename", ""), b64(b.get("content_b64")), folder_id=b.get("folder_id") or 1, title=b.get("title"),
                          doc_type=b.get("doc_type") or "GEN", tags=b.get("tags"), props=b.get("props"), description=b.get("description", ""),
                          level=b.get("level", 0), entries=b.get("entries"), inherit=b.get("inherit", True), expires_at=b.get("expires_at"),
                          comment=b.get("comment", ""))

    @route("GET", r"/api/docs/(\d+)")
    def _get(req, u, m, qs):
        return d.get(u, i(m))

    @route("PUT", r"/api/docs/(\d+)")
    def _meta(req, u, m, qs):
        b = req.body(65536)
        return d.update_meta(u, i(m), **{k: b[k] for k in ("title", "tags", "props", "description", "expires_at", "level") if k in b})

    @route("DELETE", r"/api/docs/(\d+)")
    def _trash(req, u, m, qs):
        return d.delete(u, i(m))

    @route("PUT", r"/api/docs/(\d+)/acl")
    def _acl(req, u, m, qs):
        b = req.body(65536)
        return d.set_acl(u, i(m), entries=b.get("entries"), inherit=b.get("inherit"), level=b.get("level"))

    @route("POST", r"/api/docs/(\d+)/move")
    def _move(req, u, m, qs):
        return d.move(u, i(m), req.body(4096).get("folder_id"))

    # ---- versions ----
    @route("GET", r"/api/docs/(\d+)/versions")
    def _versions(req, u, m, qs):
        return d.versions(u, i(m))

    @route("POST", r"/api/docs/(\d+)/versions")
    def _newver(req, u, m, qs):
        b = req.body(big())
        return d.new_version(u, i(m), b.get("filename", ""), b64(b.get("content_b64")), b.get("comment", ""))

    @route("GET", r"/api/docs/(\d+)/diff")
    def _diff(req, u, m, qs):
        return d.diff(u, i(m), q1(qs, "a", 0), q1(qs, "b", 0))

    @route("POST", r"/api/docs/(\d+)/restore")
    def _restore(req, u, m, qs):
        return d.restore(u, i(m), req.body(4096).get("ver"))

    @route("POST", r"/api/docs/(\d+)/checkout")
    def _checkout(req, u, m, qs):
        return d.checkout(u, i(m))

    @route("POST", r"/api/docs/(\d+)/checkout/cancel")
    def _uncheckout(req, u, m, qs):
        return d.cancel_checkout(u, i(m))

    @route("GET", r"/api/docs/(\d+)/download")
    def _download(req, u, m, qs):
        name, data, mime = d.download(u, i(m), q1(qs, "ver"))
        return Raw(data, name, mime)

    # ---- approval ----
    @route("POST", r"/api/docs/(\d+)/submit")
    def _submit(req, u, m, qs):
        b = req.body(8192)
        return d.submit(u, i(m), b.get("approvers"), b.get("mode", "sequential"), b.get("note", ""))

    @route("POST", r"/api/docs/(\d+)/approve")
    def _approve(req, u, m, qs):
        return d.approve(u, i(m), req.body(8192).get("comment", ""))

    @route("POST", r"/api/docs/(\d+)/reject")
    def _reject(req, u, m, qs):
        return d.reject(u, i(m), req.body(8192).get("comment", ""))

    @route("POST", r"/api/docs/(\d+)/delegate")
    def _delegate(req, u, m, qs):
        b = req.body(8192)
        return d.delegate(u, i(m), str(b.get("to", "")), b.get("comment", ""))

    @route("POST", r"/api/docs/(\d+)/approval/cancel")
    def _wfcancel(req, u, m, qs):
        return d.cancel_workflow(u, i(m))

    @route("GET", r"/api/dms/tasks")
    def _tasks(req, u, m, qs):
        return {"tasks": d.my_tasks(u), "requests": d.my_requests(u)}

    # ---- lifecycle ----
    @route("POST", r"/api/docs/(\d+)/hold")
    def _hold(req, u, m, qs):
        b = req.body(4096)
        return d.set_hold(u, i(m), bool(b.get("hold")), b.get("reason", ""))

    @route("POST", r"/api/docs/(\d+)/expiry")
    def _expiry(req, u, m, qs):
        return d.extend_expiry(u, i(m), req.body(4096).get("date"))

    @route("GET", r"/api/dms/trash")
    def _trashlist(req, u, m, qs):
        return d.trash(u)

    @route("POST", r"/api/docs/(\d+)/trash/restore")
    def _untrash(req, u, m, qs):
        return d.restore_doc(u, i(m))

    @route("DELETE", r"/api/docs/(\d+)/purge")
    def _purge(req, u, m, qs):
        return d.purge(u, i(m))

    # ---- collaboration ----
    @route("GET", r"/api/docs/(\d+)/comments")
    def _comments(req, u, m, qs):
        return d.comments(u, i(m))

    @route("POST", r"/api/docs/(\d+)/comments")
    def _comment(req, u, m, qs):
        b = req.body(8192)
        return d.add_comment(u, i(m), b.get("text"), b.get("parent_id"))

    @route("DELETE", r"/api/docs/(\d+)/comments/(\d+)")
    def _delcomment(req, u, m, qs):
        return d.delete_comment(u, i(m), i(m, 2))

    @route("POST", r"/api/docs/(\d+)/favorite")
    def _fav(req, u, m, qs):
        return d.set_favorite(u, i(m), bool(req.body(1024).get("on", True)))

    @route("GET", r"/api/dms/favorites")
    def _favs(req, u, m, qs):
        return d.favorites(u)

    @route("GET", r"/api/dms/recent")
    def _recent(req, u, m, qs):
        return d.recent(u, 20)

    @route("GET", r"/api/dms/prefs")
    def _prefs(req, u, m, qs):
        return d.get_prefs(u)

    @route("PUT", r"/api/dms/prefs")
    def _setprefs(req, u, m, qs):
        return d.set_prefs(u, bool(req.body(1024).get("email", True)))

    @route("GET", r"/api/dms/saved")
    def _saved(req, u, m, qs):
        return d.saved_searches(u)

    @route("POST", r"/api/dms/saved")
    def _save(req, u, m, qs):
        b = req.body(8192)
        return d.save_search(u, b.get("name"), b.get("query"))

    @route("DELETE", r"/api/dms/saved/(\d+)")
    def _unsave(req, u, m, qs):
        return d.delete_saved_search(u, i(m))

    @route("GET", r"/api/dms/notifications")
    def _notifs(req, u, m, qs):
        return d.notifications(u, q1(qs, "unread") == "1")

    @route("POST", r"/api/dms/notifications/read")
    def _read(req, u, m, qs):
        return d.mark_read(u, req.body(8192).get("ids"))

    @route("POST", r"/api/docs/(\d+)/links")
    def _link(req, u, m, qs):
        return d.link_docs(u, i(m), req.body(4096).get("other"))

    @route("DELETE", r"/api/docs/(\d+)/links/(\d+)")
    def _unlink(req, u, m, qs):
        return d.unlink_docs(u, i(m), i(m, 2))

    # ---- sharing ----
    @route("GET", r"/api/docs/(\d+)/shares")
    def _shares(req, u, m, qs):
        return d.list_shares(u, i(m))

    @route("POST", r"/api/docs/(\d+)/shares")
    def _share(req, u, m, qs):
        b = req.body(8192)
        return d.create_share(u, i(m), b.get("days", 7), b.get("max_downloads", 0), b.get("password") or None, b.get("perm", "view"), b.get("note", ""))

    @route("DELETE", r"/api/docs/(\d+)/shares/(\d+)")
    def _unshare(req, u, m, qs):
        return d.revoke_share(u, i(m), i(m, 2))

    # ---- bulk ----
    @route("POST", r"/api/dms/bulk")
    def _bulk(req, u, m, qs):
        b = req.body(262144)
        return d.bulk(u, b.get("action"), b.get("ids"), b.get("params"))

    @route("POST", r"/api/dms/export")
    def _export(req, u, m, qs):
        data, info = d.export_zip(u, req.body(262144).get("ids"))
        return Raw(data, "export.zip", "application/zip")

    @route("POST", r"/api/dms/import-zip")
    def _import(req, u, m, qs):
        b = req.body(big() * 4)
        return d.import_zip(u, b.get("folder_id") or 1, b64(b.get("content_b64")), b.get("doc_type") or "GEN", b.get("level", 0), b.get("tags"))

    # ---- admin ----
    @route("GET", r"/api/dms/admin/audit")
    def _audit(req, u, m, qs):
        return d.audit_search(u, user=q1(qs, "user"), action=q1(qs, "action"), q=q1(qs, "q"), frm=q1(qs, "from"), to=q1(qs, "to"), limit=q1(qs, "limit", 200))

    @route("GET", r"/api/dms/admin/audit\.csv")
    def _auditcsv(req, u, m, qs):
        return Raw(d.audit_csv(u, user=q1(qs, "user"), action=q1(qs, "action"), q=q1(qs, "q"), frm=q1(qs, "from"), to=q1(qs, "to")).encode("utf-8"),
                   "audit.csv", "text/csv")

    @route("GET", r"/api/dms/admin/stats")
    def _stats(req, u, m, qs):
        return d.admin_stats(u)

    @route("GET", r"/api/dms/admin/users")
    def _admin_users(req, u, m, qs):
        return d.admin_users(u)

    @route("GET", r"/api/dms/admin/system")
    def _admin_system(req, u, m, qs):
        return d.system_status(u)

    @route("POST", r"/api/dms/admin/maintenance")
    def _maint(req, u, m, qs):
        return d.maintenance(u)

    @route("PUT", r"/api/dms/admin/doctypes")
    def _doctype(req, u, m, qs):
        return d.save_doc_type(u, req.body(8192))

    return R
