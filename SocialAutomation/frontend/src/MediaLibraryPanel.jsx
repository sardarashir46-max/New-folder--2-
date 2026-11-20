import { useCallback, useEffect, useState } from "react";
import { AlertCircle, Check, FileVideo2, LoaderCircle, Pencil, Search, Trash2, Upload, X } from "lucide-react";
import { apiFetch, apiUrl, responseError } from "./api";

const rightsNames = {
  pending_review: "Pending review",
  owned: "Owned by me",
  authorized: "Authorized",
  rejected: "Rejected",
};

export default function MediaLibraryPanel({ onSessionExpired }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [busyId, setBusyId] = useState(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("");
  const [selectedFile, setSelectedFile] = useState(null);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [caption, setCaption] = useState("");
  const [editingId, setEditingId] = useState(null);
  const [editValues, setEditValues] = useState({ title: "", description: "", caption: "" });

  const loadItems = useCallback(async (signal) => {
    setError("");
    try {
      const query = new URLSearchParams();
      if (search.trim()) query.set("search", search.trim());
      if (filter) query.set("rights_status", filter);
      const suffix = query.size ? `?${query}` : "";
      const response = await apiFetch(`/api/library/media/${suffix}`, { signal });
      if (response.status === 401 || response.status === 403) onSessionExpired?.();
      if (!response.ok) throw new Error(await responseError(response));
      setItems(await response.json());
    } catch (loadError) {
      if (loadError.name !== "AbortError") setError(loadError.message || "Could not load your media library.");
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [filter, onSessionExpired, search]);

  useEffect(() => {
    const controller = new AbortController();
    loadItems(controller.signal);
    return () => controller.abort();
  }, [loadItems]);

  async function submitUpload(event) {
    event.preventDefault();
    if (!selectedFile) { setError("Choose a video file to upload."); return; }
    setUploading(true); setError(""); setNotice("");
    const form = new FormData();
    form.set("file", selectedFile);
    form.set("title", title.trim() || "Uploaded video");
    form.set("description", description);
    form.set("caption", caption);
    try {
      const response = await apiFetch("/api/library/media/", { method: "POST", body: form });
      if (response.status === 401 || response.status === 403) onSessionExpired?.();
      if (!response.ok) throw new Error(await responseError(response));
      setSelectedFile(null); setTitle(""); setDescription(""); setCaption("");
      setNotice("Video uploaded. Rights review is required before it can be scheduled.");
      await loadItems();
    } catch (uploadError) { setError(uploadError.message || "Upload failed. Please retry."); }
    finally { setUploading(false); }
  }

  function beginEdit(item) {
    setEditingId(item.id);
    setEditValues({ title: item.title, description: item.description, caption: item.caption });
    setError(""); setNotice("");
  }

  async function saveEdit(event) {
    event.preventDefault();
    const itemId = editingId;
    setBusyId(itemId); setError(""); setNotice("");
    try {
      const response = await apiFetch(`/api/library/media/${itemId}/`, {
        method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(editValues),
      });
      if (response.status === 401 || response.status === 403) onSessionExpired?.();
      if (!response.ok) throw new Error(await responseError(response));
      const updated = await response.json();
      setItems(current => current.map(item => item.id === updated.id ? updated : item));
      setEditingId(null); setNotice("Media details saved.");
    } catch (saveError) { setError(saveError.message || "Could not save media details."); }
    finally { setBusyId(null); }
  }

  async function setRights(item, rightsStatus) {
    setBusyId(item.id); setError(""); setNotice("");
    try {
      const response = await apiFetch(`/api/library/media/${item.id}/rights/`, {
        method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ rights_status: rightsStatus }),
      });
      if (response.status === 401 || response.status === 403) onSessionExpired?.();
      if (!response.ok) throw new Error(await responseError(response));
      const updated = await response.json();
      setItems(current => current.map(row => row.id === updated.id ? updated : row));
      setNotice(rightsStatus === "owned" || rightsStatus === "authorized"
        ? "Your rights declaration was recorded. It is not independently verified."
        : "Rights review updated.");
    } catch (rightsError) { setError(rightsError.message || "Could not update the rights review."); }
    finally { setBusyId(null); }
  }

  async function removeItem(item) {
    if (!window.confirm(`Remove “${item.title}” from your library? Pending schedules that use it will be cancelled.`)) return;
    setBusyId(item.id); setError(""); setNotice("");
    try {
      const response = await apiFetch(`/api/library/media/${item.id}/`, { method: "DELETE" });
      if (response.status === 401 || response.status === 403) onSessionExpired?.();
      if (!response.ok) throw new Error(await responseError(response));
      setItems(current => current.filter(row => row.id !== item.id)); setNotice("Video removed from your library.");
    } catch (deleteError) { setError(deleteError.message || "Could not remove this video."); }
    finally { setBusyId(null); }
  }

  return (
    <section className="library-workspace">
      <article className="panel library-upload-panel">
        <div className="panel-heading"><div><h3>Add original or authorized video</h3><p>Upload a video you own or have permission to redistribute.</p></div><span className="channel-status">Private upload</span></div>
        <div className="rights-declaration-note"><AlertCircle size={15}/><span>New uploads start as Pending review. Selecting Owned or Authorized records your declaration; it does not verify legal rights.</span></div>
        <form className="library-upload-form" onSubmit={submitUpload}>
          <label className="upload-file-picker"><Upload size={17}/><span>{selectedFile ? selectedFile.name : "Choose a video file"}</span><input type="file" accept=".mp4,.m4v,.mov,.webm,.mkv,.avi,video/*" onChange={event => setSelectedFile(event.target.files?.[0] || null)} /></label>
          <input value={title} onChange={event => setTitle(event.target.value)} maxLength={255} placeholder="Video title" aria-label="Video title" />
          <textarea value={description} onChange={event => setDescription(event.target.value)} placeholder="Description" aria-label="Video description" rows={2}/>
          <textarea value={caption} onChange={event => setCaption(event.target.value)} placeholder="Publishing caption" aria-label="Publishing caption" rows={2}/>
          <button className="primary-button" type="submit" disabled={uploading || !selectedFile}>{uploading ? <LoaderCircle size={15} className="spin"/> : <Upload size={15}/>} {uploading ? "Uploading…" : "Upload video"}</button>
        </form>
      </article>

      {notice && <div className="library-message success" role="status"><Check size={15}/>{notice}</div>}
      {error && <div className="library-message error" role="alert"><AlertCircle size={15}/>{error}</div>}

      <div className="library-list-toolbar">
        <div><h3>Your media</h3><p>{items.length} item{items.length === 1 ? "" : "s"}</p></div>
        <label className="library-search"><Search size={15}/><input value={search} onChange={event => setSearch(event.target.value)} placeholder="Search media" /></label>
        <select value={filter} onChange={event => setFilter(event.target.value)} aria-label="Filter by rights status">
          <option value="">All rights statuses</option>
          {Object.entries(rightsNames).map(([key, name]) => <option key={key} value={key}>{name}</option>)}
        </select>
      </div>

      {loading ? <div className="library-loading"><LoaderCircle size={18} className="spin"/> Loading your media…</div> : items.length === 0 ? (
        <div className="library-empty"><FileVideo2 size={25}/><strong>No media found</strong><span>Upload original or authorized content to start your library.</span></div>
      ) : <div className="media-card-grid">{items.map(item => (
        <article className="panel media-card" key={item.id}>
          <video className="media-preview" src={apiUrl(item.file_url)} crossOrigin="use-credentials" controls preload="metadata" />
          {editingId === item.id ? (
            <form className="media-edit-form" onSubmit={saveEdit}>
              <input value={editValues.title} onChange={event => setEditValues(values => ({ ...values, title: event.target.value }))} required maxLength={255} aria-label="Edit title" />
              <textarea value={editValues.description} onChange={event => setEditValues(values => ({ ...values, description: event.target.value }))} rows={2} aria-label="Edit description" />
              <textarea value={editValues.caption} onChange={event => setEditValues(values => ({ ...values, caption: event.target.value }))} rows={2} aria-label="Edit caption" />
              <div className="media-card-actions"><button className="primary-button" type="submit" disabled={busyId === item.id}>{busyId === item.id ? "Saving…" : "Save"}</button><button type="button" className="subtle-button" onClick={() => setEditingId(null)}><X size={13}/> Cancel</button></div>
            </form>
          ) : <>
            <div className="media-card-title-row"><h4>{item.title}</h4><span className={`media-processing ${item.processing_status}`}>{item.processing_status}</span></div>
            <p className="media-description">{item.description || "No description"}</p>
            <p className="media-details">{(item.file_size / 1024 / 1024).toFixed(1)} MB · {item.detected_content_type} · Added {new Date(item.created_at).toLocaleDateString()}</p>
            <div className={`media-rights-badge ${item.rights_status}`}>{rightsNames[item.rights_status] || item.rights_status}</div>
            {item.approved_at && <small className="media-approved-note">Declared by {item.approved_by_username || "you"} on {new Date(item.approved_at).toLocaleDateString()}</small>}
            <div className="rights-choice-row">
              <span>Rights declaration</span>
              <button disabled={busyId === item.id} className={item.rights_status === "owned" ? "rights-choice active" : "rights-choice"} onClick={() => setRights(item, "owned")}>Owned</button>
              <button disabled={busyId === item.id} className={item.rights_status === "authorized" ? "rights-choice active" : "rights-choice"} onClick={() => setRights(item, "authorized")}>Authorized</button>
              <button disabled={busyId === item.id} className={item.rights_status === "rejected" ? "rights-choice rejected active" : "rights-choice rejected"} onClick={() => setRights(item, "rejected")}>Reject</button>
              {item.rights_status !== "pending_review" && <button disabled={busyId === item.id} className="rights-choice" onClick={() => setRights(item, "pending_review")}>Reset</button>}
            </div>
            <div className="media-card-actions">
              <button className="subtle-button" onClick={() => beginEdit(item)}><Pencil size={13}/> Edit details</button>
              <button className="subtle-button media-delete-button" onClick={() => removeItem(item)} disabled={busyId === item.id}><Trash2 size={13}/> Delete</button>
            </div>
          </>}
        </article>
      ))}</div>}
    </section>
  );
}
