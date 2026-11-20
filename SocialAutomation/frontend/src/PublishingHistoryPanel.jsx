import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, AlertCircle, Clock3, LoaderCircle } from "lucide-react";
import { apiFetch, responseError } from "./api";

const labels = {
  pending: "Waiting to publish", queued: "Queued", processing: "Publishing", published: "Published",
  partial: "Partially published", failed: "Failed", cancelled: "Cancelled",
  review_required: "Review required", uncertain: "Outcome uncertain",
};

export default function PublishingHistoryPanel({ onSessionExpired }) {
  const [posts, setPosts] = useState([]);
  const [attempts, setAttempts] = useState([]);
  const [connection, setConnection] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("");

  const loadHistory = useCallback(async (signal) => {
    setError("");
    try {
      const [postsResponse, attemptsResponse, connectionResponse] = await Promise.all([
        apiFetch("/api/publishing/posts/", { signal }),
        apiFetch("/api/publishing/history/", { signal }),
        apiFetch("/api/facebook/connection/", { signal }),
      ]);
      for (const response of [postsResponse, attemptsResponse, connectionResponse]) {
        if (response.status === 401 || response.status === 403) onSessionExpired?.();
        if (!response.ok) throw new Error(await responseError(response));
      }
      const [postData, attemptData, connectionData] = await Promise.all([postsResponse.json(), attemptsResponse.json(), connectionResponse.json()]);
      setPosts(postData); setAttempts(attemptData); setConnection(connectionData);
    } catch (loadError) {
      if (loadError.name !== "AbortError") setError(loadError.message || "Could not load publishing history.");
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [onSessionExpired]);

  useEffect(() => {
    const controller = new AbortController();
    loadHistory(controller.signal);
    return () => controller.abort();
  }, [loadHistory]);

  const filteredPosts = useMemo(() => filter ? posts.filter(post => post.status === filter) : posts, [filter, posts]);
  const attemptByPost = useMemo(() => attempts.reduce((groups, attempt) => {
    groups[attempt.post_id] ||= [];
    groups[attempt.post_id].push(attempt);
    return groups;
  }, {}), [attempts]);

  return <section className="panel activity-history-panel">
    <div className="panel-heading"><div><h3>Publishing activity</h3><p>Scheduled posts and each Page publishing attempt.</p></div><span className="channel-status">{attempts.length} attempts</span></div>
    <div className="history-disabled-note"><AlertCircle size={15}/><span>
      {!connection?.publishing_enabled ? "Publishing disabled: posts wait without Meta attempts." :
        connection?.requires_reconnection ? "Required permissions missing: reconnect and grant the required Page permissions." :
          !connection?.connected ? "Facebook connection missing: reconnect before publishing." :
            "Meta success is recorded only with a confirmed video ID. Review required means check Page activity before any retry."}
    </span></div>
    <div className="history-filter-row"><label htmlFor="history-status">Status</label><select id="history-status" value={filter} onChange={event => setFilter(event.target.value)}><option value="">All statuses</option>{Object.entries(labels).map(([key, value]) => <option key={key} value={key}>{value}</option>)}</select></div>
    {error && <div className="library-message error" role="alert"><AlertCircle size={15}/>{error}</div>}
    {loading ? <div className="library-loading"><LoaderCircle size={18} className="spin"/> Loading activity…</div> : filteredPosts.length === 0 ? <div className="library-empty"><Clock3 size={25}/><strong>No publishing activity</strong><span>Scheduled posts and attempts will appear here.</span></div> : <div className="history-list">{filteredPosts.map(post => (
      <article className="history-post" key={post.id}>
        <div className="history-post-icon"><Activity size={17}/></div>
        <div className="history-post-body"><div className="history-post-heading"><strong>{post.media_title}</strong><span className={`channel-status ${post.status === "published" ? "is-connected" : ""}`}>{labels[post.status] || post.status}</span></div>
          <p>{post.caption || "No caption"}</p><small>Scheduled {new Date(post.scheduled_for).toLocaleString()}</small>
          {post.last_error && <div className="history-error-detail">{post.last_error}</div>}
          <div className="history-target-list">{post.targets.map(target => <div className="history-target" key={target.id}><span><strong>{target.page_name}</strong> · {labels[target.status] || target.status}</span>{target.external_post_id && <code>Meta ID {target.external_post_id}</code>}{target.last_error && <small>{target.last_error}</small>}</div>)}</div>
          {(attemptByPost[post.id] || []).length > 0 && <details className="history-attempts"><summary>{attemptByPost[post.id].length} recorded attempt{attemptByPost[post.id].length === 1 ? "" : "s"}</summary>{attemptByPost[post.id].map(attempt => <div key={attempt.id}><span>Attempt {attempt.attempt_number} · {labels[attempt.status] || attempt.status} · {attempt.page_name}</span><time>{new Date(attempt.started_at).toLocaleString()}</time>{attempt.error && <small>{attempt.error}</small>}</div>)}</details>}
        </div>
      </article>
    ))}</div>}
  </section>;
}
