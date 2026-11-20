import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, CalendarDays, Check, ChevronLeft, ChevronRight, Clock3, LoaderCircle, Pencil, Plus, X } from "lucide-react";
import { apiFetch, responseError } from "./api";

const statusLabel = {
  pending: "Waiting to publish", queued: "Queued", processing: "Publishing",
  published: "Published", partial: "Partially published", failed: "Failed", review_required: "Review required", cancelled: "Cancelled",
};

function initialLocalSchedule() {
  const date = new Date(Date.now() + 60 * 60 * 1000);
  date.setMinutes(Math.ceil(date.getMinutes() / 15) * 15, 0, 0);
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 16);
}

function toLocalInput(value) {
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

export default function PublishingCalendarPanel({ onSessionExpired }) {
  const [posts, setPosts] = useState([]);
  const [media, setMedia] = useState([]);
  const [pages, setPages] = useState([]);
  const [connection, setConnection] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [busyId, setBusyId] = useState(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [view, setView] = useState("calendar");
  const [month, setMonth] = useState(() => new Date(new Date().getFullYear(), new Date().getMonth(), 1));
  const [editingId, setEditingId] = useState(null);
  const [mediaId, setMediaId] = useState("");
  const [caption, setCaption] = useState("");
  const [description, setDescription] = useState("");
  const [scheduledFor, setScheduledFor] = useState(initialLocalSchedule);
  const [pageIds, setPageIds] = useState([]);
  const idempotencyKey = useRef(window.crypto.randomUUID());

  const loadData = useCallback(async (signal) => {
    setError("");
    try {
      const [postResponse, mediaResponse, pageResponse, connectionResponse] = await Promise.all([
        apiFetch("/api/publishing/posts/", { signal }),
        apiFetch("/api/library/media/", { signal }),
        apiFetch("/api/facebook/pages/", { signal }),
        apiFetch("/api/facebook/connection/", { signal }),
      ]);
      for (const response of [postResponse, mediaResponse, pageResponse, connectionResponse]) {
        if (response.status === 401 || response.status === 403) onSessionExpired?.();
        if (!response.ok) throw new Error(await responseError(response));
      }
      const [postData, mediaData, pageData, connectionData] = await Promise.all([
        postResponse.json(), mediaResponse.json(), pageResponse.json(), connectionResponse.json(),
      ]);
      setPosts(postData); setMedia(mediaData); setPages(pageData); setConnection(connectionData);
    } catch (loadError) {
      if (loadError.name !== "AbortError") setError(loadError.message || "Could not load your publishing calendar.");
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [onSessionExpired]);

  useEffect(() => {
    const controller = new AbortController();
    loadData(controller.signal);
    return () => controller.abort();
  }, [loadData]);

  const approvedMedia = useMemo(() => media.filter(item =>
    item.processing_status === "ready" && ["owned", "authorized"].includes(item.rights_status)), [media]);
  const selectedPages = useMemo(() => pages.filter(page => page.available && page.selected), [pages]);
  const monthPosts = useMemo(() => posts.filter(post => {
    const date = new Date(post.scheduled_for);
    return date.getFullYear() === month.getFullYear() && date.getMonth() === month.getMonth();
  }), [posts, month]);

  function resetForm() {
    setEditingId(null); setMediaId(""); setCaption(""); setDescription(""); setScheduledFor(initialLocalSchedule()); setPageIds([]);
    idempotencyKey.current = window.crypto.randomUUID();
  }

  function updateFormValue(setter, value) {
    idempotencyKey.current = window.crypto.randomUUID();
    setter(value);
  }

  function startEdit(post) {
    if (post.status !== "pending") return;
    setEditingId(post.id); setMediaId(String(post.media)); setCaption(post.caption); setDescription(post.description);
    setScheduledFor(toLocalInput(post.scheduled_for)); setPageIds(post.targets.filter(target => target.status !== "cancelled").map(target => pages.find(page => page.page_id === target.page_id)?.id).filter(Boolean));
    setError(""); setNotice("");
    document.getElementById("scheduled-post-form")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function togglePage(id) {
    idempotencyKey.current = window.crypto.randomUUID();
    setPageIds(current => current.includes(id) ? current.filter(value => value !== id) : [...current, id]);
  }

  async function submitPost(event) {
    event.preventDefault(); setSaving(true); setError(""); setNotice("");
    const payload = {
      media: Number(mediaId), caption, description,
      scheduled_for: new Date(scheduledFor).toISOString(),
      target_pages: pageIds,
    };
    if (!editingId) payload.idempotency_key = idempotencyKey.current;
    try {
      const response = await apiFetch(editingId ? `/api/publishing/posts/${editingId}/` : "/api/publishing/posts/", {
        method: editingId ? "PATCH" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (response.status === 401 || response.status === 403) onSessionExpired?.();
      if (!response.ok) throw new Error(await responseError(response));
      setNotice(editingId ? "Scheduled post updated." : "Post added to the calendar.");
      resetForm(); await loadData();
    } catch (saveError) { setError(saveError.message || "Could not save the scheduled post."); }
    finally { setSaving(false); }
  }

  async function cancelPost(post) {
    if (!window.confirm(`Cancel the scheduled post for “${post.media_title}”?`)) return;
    setBusyId(post.id); setError(""); setNotice("");
    try {
      const response = await apiFetch(`/api/publishing/posts/${post.id}/cancel/`, { method: "POST", body: "{}" });
      if (response.status === 401 || response.status === 403) onSessionExpired?.();
      if (!response.ok) throw new Error(await responseError(response));
      const updated = await response.json(); setPosts(current => current.map(item => item.id === updated.id ? updated : item));
      setNotice("Scheduled post cancelled.");
    } catch (cancelError) { setError(cancelError.message || "Could not cancel this scheduled post."); }
    finally { setBusyId(null); }
  }

  const firstWeekday = new Date(month.getFullYear(), month.getMonth(), 1).getDay();
  const dayCount = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate();
  const calendarCells = Array.from({ length: firstWeekday + dayCount }, (_, index) => index < firstWeekday ? null : index - firstWeekday + 1);

  return (
    <section className="publishing-workspace">
      <article className="panel scheduling-panel" id="scheduled-post-form">
        <div className="panel-heading"><div><h3>{editingId ? "Edit scheduled post" : "Schedule a post"}</h3><p>Choose approved media, selected Pages, and a future time.</p></div>{editingId && <button className="subtle-button" onClick={resetForm}><X size={14}/> Stop editing</button>}</div>
        {connection && <div className="publishing-disabled-note"><AlertCircle size={15}/><span>
          {!connection.publishing_enabled ? "Publishing disabled: scheduled posts remain waiting and no Meta request is made." :
            connection.requires_reconnection ? "Required permissions missing: reconnect and grant pages_manage_posts and the other requested Page permissions." :
              !connection.connected ? "Facebook connection missing: connect an account before publishing." :
                !connection.publishing_configured ? "Meta server configuration is incomplete. Publishing cannot start until credentials and token encryption are configured." :
                  "Publishing enabled: posts move to Publishing when a worker starts them; Published is shown only after Meta confirms a video ID."}
        </span></div>}
        <form className="schedule-form" onSubmit={submitPost}>
          <label>Approved video<select value={mediaId} onChange={event => updateFormValue(setMediaId, event.target.value)} required disabled={!approvedMedia.length}><option value="">Select media…</option>{approvedMedia.map(item => <option key={item.id} value={item.id}>{item.title} · {item.rights_status}</option>)}</select></label>
          <label>Post caption<textarea value={caption} onChange={event => updateFormValue(setCaption, event.target.value)} rows={3} placeholder="Write a caption for the selected Pages" /></label>
          <label>Description<textarea value={description} onChange={event => updateFormValue(setDescription, event.target.value)} rows={2} placeholder="Optional details" /></label>
          <label>Publish date and time<input type="datetime-local" value={scheduledFor} onChange={event => updateFormValue(setScheduledFor, event.target.value)} required /></label>
          <fieldset className="page-target-picker"><legend>Selected Facebook Pages</legend>
            {selectedPages.length === 0 ? <span className="muted">No selected Pages. Select Pages in Facebook Pages first.</span> : selectedPages.map(page => (
              <label key={page.id}><input type="checkbox" checked={pageIds.includes(page.id)} disabled={!page.can_create_content} onChange={() => togglePage(page.id)}/><span>{page.name}{!page.can_create_content ? " · CREATE_CONTENT missing" : ""}</span></label>
            ))}
          </fieldset>
          <div className="schedule-form-actions"><button className="primary-button" type="submit" disabled={saving || !mediaId || !pageIds.length || !connection?.connected}>{saving ? <LoaderCircle size={15} className="spin"/> : editingId ? <Check size={15}/> : <Plus size={15}/>} {saving ? "Saving…" : editingId ? "Save changes" : "Add to calendar"}</button>{editingId && <button type="button" className="subtle-button" onClick={resetForm}>Cancel edit</button>}</div>
        </form>
      </article>

      {notice && <div className="library-message success" role="status"><Check size={15}/>{notice}</div>}
      {error && <div className="library-message error" role="alert"><AlertCircle size={15}/>{error}</div>}

      <article className="panel calendar-panel">
        <div className="calendar-toolbar"><div className="calendar-view-switch"><button className={view === "calendar" ? "selected" : ""} onClick={() => setView("calendar")}><CalendarDays size={14}/> Calendar</button><button className={view === "list" ? "selected" : ""} onClick={() => setView("list")}><Clock3 size={14}/> List</button></div>
          <div className="calendar-month-controls"><button className="icon-button" onClick={() => setMonth(current => new Date(current.getFullYear(), current.getMonth() - 1, 1))} aria-label="Previous month"><ChevronLeft size={16}/></button><strong>{month.toLocaleDateString(undefined, { month: "long", year: "numeric" })}</strong><button className="icon-button" onClick={() => setMonth(current => new Date(current.getFullYear(), current.getMonth() + 1, 1))} aria-label="Next month"><ChevronRight size={16}/></button></div>
        </div>
        {loading ? <div className="library-loading"><LoaderCircle size={18} className="spin"/> Loading schedule…</div> : posts.length === 0 ? <div className="library-empty"><CalendarDays size={25}/><strong>Your publishing calendar is clear</strong><span>Schedule an approved video to see it here.</span></div> : view === "calendar" ? (
          <div className="calendar-grid"><div className="calendar-weekdays">{["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map(day => <span key={day}>{day}</span>)}</div><div className="calendar-days">{calendarCells.map((day, index) => {
            const dayPosts = day ? monthPosts.filter(post => new Date(post.scheduled_for).getDate() === day) : [];
            return <div key={`${month.toISOString()}-${index}`} className={`calendar-day ${day ? "" : "calendar-day-blank"}`}><span className="calendar-day-number">{day || ""}</span>{dayPosts.slice(0, 3).map(post => <button key={post.id} className={`calendar-event status-${post.status}`} title={`${post.media_title} · ${statusLabel[post.status]}`} onClick={() => startEdit(post)}>{new Date(post.scheduled_for).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })} {post.media_title}</button>)}{dayPosts.length > 3 && <small>+{dayPosts.length - 3} more</small>}</div>;
          })}</div></div>
        ) : <div className="scheduled-post-list">{posts.map(post => (
          <article className="scheduled-post-row" key={post.id}>
            <div className="scheduled-post-date"><strong>{new Date(post.scheduled_for).toLocaleDateString(undefined, { month: "short", day: "numeric" })}</strong><span>{new Date(post.scheduled_for).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</span></div>
            <div className="scheduled-post-main"><strong>{post.media_title}</strong><span>{post.caption || "No caption"}</span><div className="scheduled-targets">{post.targets.map(target => <span key={target.id} className={`target-status status-${target.status}`}>{target.page_name}: {statusLabel[target.status] || target.status}</span>)}</div>{post.last_error && <small className="scheduled-post-error">{post.last_error}</small>}</div>
            <span className={`channel-status ${post.status === "published" ? "is-connected" : ""}`}>{statusLabel[post.status] || post.status}</span>
            {post.status === "pending" && <div className="scheduled-post-actions"><button className="subtle-button" onClick={() => startEdit(post)}><Pencil size={13}/> Edit</button><button className="subtle-button media-delete-button" onClick={() => cancelPost(post)} disabled={busyId === post.id}>{busyId === post.id ? "Cancelling…" : "Cancel"}</button></div>}
          </article>
        ))}</div>}
      </article>
      <p className="publishing-rights-note">Only media marked Owned or Authorized can be scheduled. These are user declarations, not verified proof. YouTube discovery remains metadata-only.</p>
    </section>
  );
}
