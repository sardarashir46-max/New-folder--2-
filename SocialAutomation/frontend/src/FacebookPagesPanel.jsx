import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, Check, ExternalLink, Facebook, LoaderCircle, RefreshCw, ShieldCheck, Unplug } from "lucide-react";
import { apiFetch, apiUrl, responseError } from "./api";

const callbackMessages = {
  connected: ["Facebook connected. Your available Pages are shown below.", "success"],
  cancelled: ["Facebook connection was cancelled.", "error"],
  "state-error": ["The Facebook sign-in session expired or could not be verified. Please try again.", "error"],
  "auth-required": ["Sign in again before connecting Facebook.", "error"],
  "configuration-error": ["Facebook Login is not configured on this server yet.", "error"],
  "connection-error": ["Facebook could not complete the connection. Check permissions and try again.", "error"],
  "permissions-missing": ["Meta did not grant all required permissions. Publishing requires pages_show_list, pages_read_engagement, and pages_manage_posts. Reconnect and approve them; Meta app review or access may be required.", "error"],
  "callback-error": ["Facebook returned an incomplete response. Please reconnect.", "error"],
};

export default function FacebookPagesPanel({ onSessionExpired, callbackStatus }) {
  const sessionExpiredHandler = useRef(onSessionExpired);
  sessionExpiredHandler.current = onSessionExpired;
  const [connection, setConnection] = useState(null);
  const [pages, setPages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [busyPage, setBusyPage] = useState(null);
  const [bulkUpdating, setBulkUpdating] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState(callbackMessages[callbackStatus]?.[0] || "");
  const [noticeType, setNoticeType] = useState(callbackMessages[callbackStatus]?.[1] || "success");

  const handleResponse = useCallback(async (response) => {
    if (response.status === 401 || response.status === 403) sessionExpiredHandler.current?.();
    if (!response.ok) throw new Error(await responseError(response));
    return response.json();
  }, []);

  const loadData = useCallback(async (signal) => {
    setError("");
    try {
      const [connectionResponse, pagesResponse] = await Promise.all([
        apiFetch("/api/facebook/connection/", { signal }),
        apiFetch("/api/facebook/pages/", { signal }),
      ]);
      const [connectionData, pageData] = await Promise.all([
        handleResponse(connectionResponse), handleResponse(pagesResponse),
      ]);
      setConnection(connectionData);
      setPages(pageData);
    } catch (loadError) {
      if (loadError.name !== "AbortError") setError(loadError.message || "Could not load Facebook Pages.");
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [handleResponse]);

  useEffect(() => {
    const controller = new AbortController();
    loadData(controller.signal);
    return () => controller.abort();
  }, [loadData]);

  async function refreshPages() {
    setRefreshing(true); setError(""); setNotice("");
    try {
      const result = await handleResponse(await apiFetch("/api/facebook/pages/refresh/", { method: "POST", body: "{}" }));
      setPages(result.pages || []); setConnection(result.connection);
      setNotice("Facebook Pages refreshed."); setNoticeType("success");
    } catch (refreshError) { setError(refreshError.message || "Could not refresh Pages."); }
    finally { setRefreshing(false); }
  }

  async function changeSelection(page, selected) {
    setBusyPage(page.id); setError(""); setNotice("");
    try {
      const result = await handleResponse(await apiFetch(`/api/facebook/pages/${page.id}/selection/`, {
        method: "POST", body: JSON.stringify({ selected }),
      }));
      setPages(current => current.map(item => item.id === result.id ? result : item));
      setNotice(selected ? `${page.name} selected for a future workflow.` : `${page.name} deselected.`);
      setNoticeType("success");
    } catch (selectionError) { setError(selectionError.message || "Could not update Page selection."); }
    finally { setBusyPage(null); }
  }

  async function changeAllSelections(selected) {
    const targets = pages.filter(page => page.available && page.selected !== selected);
    if (!targets.length) return;
    setBulkUpdating(true); setError(""); setNotice("");
    try {
      const changed = [];
      for (const page of targets) {
        changed.push(await handleResponse(await apiFetch(`/api/facebook/pages/${page.id}/selection/`, {
          method: "POST", body: JSON.stringify({ selected }),
        })));
      }
      const changedById = new Map(changed.map(page => [page.id, page]));
      setPages(current => current.map(page => changedById.get(page.id) || page));
      setNotice(selected ? "All available Pages selected." : "All Pages deselected.");
      setNoticeType("success");
    } catch (bulkError) {
      setError(bulkError.message || "Some Page selections could not be updated. Refresh to see the latest status.");
      await loadData();
    } finally { setBulkUpdating(false); }
  }

  async function disconnect() {
    if (!window.confirm("Disconnect Facebook and remove its locally stored Page records?")) return;
    setDisconnecting(true); setError(""); setNotice("");
    try {
      const result = await handleResponse(await apiFetch("/api/facebook/connection/", { method: "DELETE" }));
      setConnection({ connected: false, status: "revoked", page_count: 0, selected_page_count: 0 });
      setPages([]);
      setNotice(result.revocation_confirmed
        ? "Facebook disconnected and the requested permissions were revoked."
        : "Facebook disconnected and local Page records were removed. Meta revocation could not be confirmed; remove app access in Facebook settings if needed.");
      setNoticeType(result.revocation_confirmed ? "success" : "error");
    } catch (disconnectError) { setError(disconnectError.message || "Could not disconnect Facebook."); }
    finally { setDisconnecting(false); }
  }

  const connected = Boolean(connection?.connected);
  return (
    <section className="panel facebook-pages-panel">
      <div className="panel-heading facebook-panel-heading">
        <div><h3>Facebook Page access</h3><p>Connect through Meta and choose Pages you manage.</p></div>
        <span className={`channel-status ${connected ? "is-connected" : ""}`}>{connection?.status || "Not connected"}</span>
      </div>
      <div className="facebook-security-note"><ShieldCheck size={16}/><span>Official Meta authorization. Publishing requires pages_show_list, pages_read_engagement, and pages_manage_posts. Tokens remain encrypted on the server.</span></div>
      {notice && <div className={`facebook-message ${noticeType}`} role="status">{noticeType === "error" ? <AlertCircle size={15}/> : <Check size={15}/>}<span>{notice}</span></div>}
      {error && <div className="facebook-message error" role="alert"><AlertCircle size={15}/><span>{error}</span></div>}
      {connection && !connection.publishing_enabled && <div className="facebook-message" role="status"><AlertCircle size={15}/><span>Publishing disabled on this server. Scheduled posts remain waiting.</span></div>}
      {connection && !connected && !connection.requires_reconnection && <div className="facebook-message" role="status"><AlertCircle size={15}/><span>Facebook connection missing. Connect an account to authorize Page access.</span></div>}
      {connection?.requires_reconnection && <div className="facebook-message error" role="status"><AlertCircle size={15}/><span>Required permissions missing. Reconnect Facebook and grant pages_show_list, pages_read_engagement, and pages_manage_posts.</span></div>}

      <div className="facebook-toolbar">
        {!connected ? (
          <button className="primary-button" onClick={() => window.location.assign(apiUrl("/api/facebook/oauth/start/"))}><Facebook size={16}/> Connect Facebook</button>
        ) : <>
          <span className="facebook-summary">Facebook account {connection.facebook_user_id} · {connection.selected_page_count || 0} selected of {connection.page_count || 0} available Pages</span>
          <div className="facebook-actions">
            <button className="subtle-button" onClick={() => changeAllSelections(true)} disabled={bulkUpdating || refreshing}><Check size={14}/>{bulkUpdating ? "Updating…" : "Select all"}</button>
            <button className="subtle-button" onClick={() => changeAllSelections(false)} disabled={bulkUpdating || refreshing}>Deselect all</button>
            <button className="subtle-button" onClick={refreshPages} disabled={refreshing}><RefreshCw size={14} className={refreshing ? "spin" : ""}/>{refreshing ? "Refreshing…" : "Refresh Pages"}</button>
            <button className="subtle-button facebook-disconnect" onClick={disconnect} disabled={disconnecting}><Unplug size={14}/>{disconnecting ? "Disconnecting…" : "Disconnect"}</button>
          </div>
        </>}
      </div>

      {loading ? <div className="facebook-loading"><LoaderCircle size={18} className="spin"/> Loading Facebook Pages…</div> : !connected ? (
        <div className="facebook-empty"><div className="placeholder-icon"><Facebook/></div><strong>Connect your Facebook account</strong><span>We will show the Pages Meta confirms you can manage.</span></div>
      ) : pages.length === 0 ? (
        <div className="facebook-empty"><div className="placeholder-icon"><Facebook/></div><strong>No Pages available</strong><span>Check that this account has Page access, then refresh the list.</span></div>
      ) : (
        <div className="facebook-page-list">{pages.map(page => (
          <article className="facebook-page-card" key={page.id}>
            {page.picture_url ? <img src={page.picture_url} alt="" referrerPolicy="no-referrer"/> : <div className="facebook-page-placeholder"><Facebook size={18}/></div>}
            <div className="facebook-page-copy"><strong>{page.name}</strong><span>{page.category || "Facebook Page"} · ID {page.page_id}</span><span className={`facebook-selected-label ${page.selected ? "selected" : ""}`}>{page.selected ? "Selected" : "Not selected"}</span></div>
            <span className={`facebook-selected-label ${page.can_create_content ? "selected" : ""}`}>{page.can_create_content ? "Can create content" : "CREATE_CONTENT unavailable"}</span>
            <a className="facebook-open-link" href={`https://www.facebook.com/${encodeURIComponent(page.page_id)}`} target="_blank" rel="noreferrer" aria-label={`Open ${page.name} on Facebook`}><ExternalLink size={15}/></a>
            <button className={page.selected ? "subtle-button facebook-selection selected" : "subtle-button facebook-selection"} disabled={busyPage === page.id || bulkUpdating || !page.available} onClick={() => changeSelection(page, !page.selected)}>
              {busyPage === page.id ? <LoaderCircle size={14} className="spin"/> : page.selected ? "Deselect" : "Select"}
            </button>
          </article>
        ))}</div>
      )}
      <p className="facebook-rights-note">Page selection records your preference. Publishing also requires CREATE_CONTENT task access and Meta granted permissions.</p>
    </section>
  );
}
