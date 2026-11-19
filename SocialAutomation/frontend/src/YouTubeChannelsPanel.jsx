import { useEffect, useState } from "react";
import {
  ExternalLink,
  LoaderCircle,
  ListVideo,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Trash2,
  X,
  Youtube,
} from "lucide-react";

import { apiFetch, responseError } from "./api";

const CHANNELS_URL = "/api/channels/";

function numberText(value) {
  return Number(value || 0).toLocaleString();
}

function dateText(value) {
  if (!value) return "Publication date unavailable";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Publication date unavailable";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(date);
}

function dateTimeText(value) {
  if (!value) return "No successful check yet";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "No successful check yet";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function handleText(channel) {
  if (!channel.custom_url) return channel.channel_id;
  return channel.custom_url.startsWith("@") ? channel.custom_url : `@${channel.custom_url}`;
}

export default function YouTubeChannelsPanel({ onSessionExpired, onNotice }) {
  const [channels, setChannels] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [summary, setSummary] = useState(null);
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [summaryError, setSummaryError] = useState("");
  const [channelUrl, setChannelUrl] = useState("");
  const [adding, setAdding] = useState(false);
  const [actionError, setActionError] = useState("");
  const [busyAction, setBusyAction] = useState("");
  const [selectedChannel, setSelectedChannel] = useState(null);
  const [videos, setVideos] = useState([]);
  const [videosLoading, setVideosLoading] = useState(false);
  const [videosError, setVideosError] = useState("");

  async function request(path, options = {}) {
    const response = await apiFetch(path, options);
    if (response.status === 401 || response.status === 403) {
      onSessionExpired();
      throw new Error("Your session expired. Sign in again to manage your channels.");
    }
    if (!response.ok) throw new Error(await responseError(response));
    if (response.status === 204) return null;
    return response.json();
  }

  async function loadChannels(signal, quiet = false) {
    if (!quiet) setLoading(true);
    setLoadError("");
    try {
      const data = await request(CHANNELS_URL, { signal });
      if (!signal?.aborted) setChannels(Array.isArray(data) ? data : data.results || []);
    } catch (error) {
      if (error.name !== "AbortError" && !signal?.aborted) setLoadError(error.message);
    } finally {
      if (!quiet && !signal?.aborted) setLoading(false);
    }
  }

  async function loadSummary(signal, quiet = false) {
    if (!quiet) setSummaryLoading(true);
    setSummaryError("");
    try {
      const data = await request(`${CHANNELS_URL}monitoring-summary/`, { signal });
      if (!signal?.aborted) setSummary(data);
    } catch (error) {
      if (error.name !== "AbortError" && !signal?.aborted) setSummaryError(error.message);
    } finally {
      if (!quiet && !signal?.aborted) setSummaryLoading(false);
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    loadChannels(controller.signal);
    loadSummary(controller.signal);
    const pollId = window.setInterval(() => {
      loadChannels(undefined, true);
      loadSummary(undefined, true);
    }, 60000);
    return () => {
      controller.abort();
      window.clearInterval(pollId);
    };
  }, []);

  async function addChannel(event) {
    event.preventDefault();
    const value = channelUrl.trim();
    if (!value || adding) return;

    setAdding(true);
    setActionError("");
    try {
      const channel = await request(CHANNELS_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ channel_url: value }),
      });
      setChannels((current) => [channel, ...current.filter((item) => item.id !== channel.id)]);
      setChannelUrl("");
      loadSummary();
      onNotice("YouTube channel details loaded successfully.");
    } catch (error) {
      setActionError(error.message || "Could not add this channel. Please try again.");
    } finally {
      setAdding(false);
    }
  }

  async function runChannelAction(channel, action, path, options, successMessage) {
    if (busyAction) return;
    setBusyAction(`${channel.id}:${action}`);
    setActionError("");
    try {
      const updated = await request(path, options);
      if (action === "remove") {
        setChannels((current) => current.filter((item) => item.id !== channel.id));
        if (selectedChannel?.id === channel.id) closeVideos();
      } else if (updated) {
        setChannels((current) => current.map((item) => item.id === channel.id ? updated : item));
      }
      loadSummary();
      onNotice(successMessage);
    } catch (error) {
      setActionError(error.message || "That channel action could not be completed.");
    } finally {
      setBusyAction("");
    }
  }

  function refreshChannel(channel) {
    return runChannelAction(
      channel,
      "refresh",
      `${CHANNELS_URL}${channel.id}/refresh/`,
      { method: "POST" },
      `Refreshed ${channel.title}.`,
    );
  }

  function toggleMonitoring(channel) {
    const enabled = !channel.monitoring_enabled;
    return runChannelAction(
      channel,
      "monitoring",
      `${CHANNELS_URL}${channel.id}/monitoring/`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      },
      `${channel.title} monitoring ${enabled ? "resumed" : "paused"}.`,
    );
  }

  function removeChannel(channel) {
    const confirmed = window.confirm(`Remove ${channel.title} from your channel list?`);
    if (!confirmed) return;
    return runChannelAction(
      channel,
      "remove",
      `${CHANNELS_URL}${channel.id}/`,
      { method: "DELETE" },
      `${channel.title} was removed.`,
    );
  }

  async function updateRightsStatus(channel, video, rightsStatus) {
    try {
      const updated = await request(`${CHANNELS_URL}${channel.id}/videos/`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ video_id: video.video_id, rights_status: rightsStatus }),
      });
      setVideos((current) => current.map((item) => item.id === updated.id ? updated : item));
      onNotice("Rights status saved as your own attestation.");
    } catch (error) {
      setVideosError(error.message || "Could not save the rights status.");
    }
  }

  async function viewVideos(channel) {
    setSelectedChannel(channel);
    setVideos([]);
    setVideosError("");
    setVideosLoading(true);
    try {
      const data = await request(`${CHANNELS_URL}${channel.id}/videos/`);
      setVideos(Array.isArray(data) ? data : data.results || []);
    } catch (error) {
      setVideosError(error.message || "Could not load this channel's videos.");
    } finally {
      setVideosLoading(false);
    }
  }

  function closeVideos() {
    setSelectedChannel(null);
    setVideos([]);
    setVideosError("");
    setVideosLoading(false);
  }

  return (
    <section className="panel channels-panel">
      <div className="panel-heading channel-panel-heading">
        <div>
          <h3>Your YouTube channels</h3>
          <p>Add public channel metadata and manage automatic background checks.</p>
        </div>
        <span className="channel-total">{channels.length} {channels.length === 1 ? "channel" : "channels"}</span>
      </div>

      <form className="channel-add-form" onSubmit={addChannel}>
        <label className="channel-url-label" htmlFor="youtube-channel-url">YouTube channel URL, @handle, or channel ID</label>
        <div className="channel-add-controls">
          <input
            id="youtube-channel-url"
            type="text"
            autoComplete="url"
            placeholder="youtube.com/@channel or UC…"
            value={channelUrl}
            onChange={(event) => setChannelUrl(event.target.value)}
            disabled={adding}
            required
          />
          <button className="primary-button" type="submit" disabled={adding || !channelUrl.trim()}>
            {adding ? <><LoaderCircle className="spin" size={15} /> Loading channel…</> : <><Plus size={16} /> Add channel</>}
          </button>
        </div>
        <p className="channel-form-hint">Channel details come from YouTube’s public metadata API. No Google account is connected.</p>
      </form>

      <section className="monitoring-overview" aria-label="YouTube monitoring overview">
        <article className="monitoring-stat"><span>Selected channels</span><strong>{summaryLoading ? "..." : summary?.total_channels ?? channels.length}</strong></article>
        <article className="monitoring-stat"><span>Monitoring active</span><strong>{summaryLoading ? "..." : summary?.active_monitoring_channels ?? 0}</strong></article>
        <article className="monitoring-stat"><span>Discovered videos</span><strong>{summaryLoading ? "..." : numberText(summary?.total_discovered_videos)}</strong></article>
        <article className="monitoring-stat monitoring-last-check"><span>Last successful check</span><strong>{summaryLoading ? "Loading..." : dateTimeText(summary?.last_successful_check_at)}</strong></article>
      </section>
      {summary?.monitoring_service_enabled === false && (
        <div className="channel-inline-error" role="status">Automatic monitoring is disabled by the server configuration. Your channel preferences are saved and will take effect when the service is enabled.</div>
      )}
      {summaryError && (
        <div className="channel-inline-error" role="alert">
          <span>Monitoring summary could not be loaded: {summaryError}</span>
          <button className="subtle-button" type="button" onClick={() => loadSummary()}>Retry summary</button>
        </div>
      )}

      {actionError && <div className="channel-inline-error" role="alert">{actionError}</div>}
      {loading ? (
        <div className="channels-message" role="status"><LoaderCircle className="spin" size={18} /> Loading your channels…</div>
      ) : loadError ? (
        <div className="channels-message channels-error" role="alert">
          <strong>Unable to load channels</strong><span>{loadError}</span>
          <button className="subtle-button" type="button" onClick={() => loadChannels()}>Try again</button>
        </div>
      ) : channels.length === 0 ? (
        <div className="channels-message channel-empty-state">
          <div className="placeholder-icon"><Youtube /></div>
          <strong>No channels yet</strong>
          <span>Add a channel URL, @handle, or channel ID above to get started.</span>
        </div>
      ) : (
        <div className="channel-list">
          {channels.map((channel) => {
            const busy = busyAction.startsWith(`${channel.id}:`);
            const isMonitoring = channel.monitoring_enabled && summary?.monitoring_service_enabled !== false;
            return (
              <article className="channel-card channel-management-card" key={channel.id}>
                {channel.thumbnail_url ? <img src={channel.thumbnail_url} alt={`${channel.title} thumbnail`} loading="lazy" /> : <div className="channel-thumbnail"><Youtube size={21} /></div>}
                <div className="channel-details">
                  <strong>{channel.title}</strong>
                  <span>{handleText(channel)}</span>
                  <small>
                    {numberText(channel.subscriber_count)} subscribers · {numberText(channel.video_count)} videos · {numberText(channel.view_count)} views
                  </small>
                  <span className={`channel-monitoring ${isMonitoring ? "monitoring-on" : "monitoring-off"}`}>
                    {summary?.monitoring_service_enabled === false ? "Monitoring service disabled" : channel.monitoring_enabled ? "Monitoring active" : "Monitoring paused"}
                  </span>
                  <span className="channel-last-check">Last checked: {dateTimeText(channel.last_checked_at)}</span>
                </div>
                <div className="channel-actions">
                  <button className="channel-action-button" type="button" onClick={() => refreshChannel(channel)} disabled={Boolean(busyAction)} title="Refresh channel" aria-label={`Refresh ${channel.title}`}>
                    {busy && busyAction.endsWith(":refresh") ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={15} />}<span>Refresh</span>
                  </button>
                  <button className="channel-action-button" type="button" onClick={() => viewVideos(channel)} disabled={videosLoading || Boolean(busyAction)} title="View videos" aria-label={`View videos for ${channel.title}`}>
                    {videosLoading && selectedChannel?.id === channel.id ? <LoaderCircle className="spin" size={15} /> : <ListVideo size={15} />}<span>Videos</span>
                  </button>
                  <button className="channel-action-button" type="button" onClick={() => toggleMonitoring(channel)} disabled={Boolean(busyAction)} aria-label={`${channel.monitoring_enabled ? "Pause" : "Resume"} monitoring for ${channel.title}`}>
                    {channel.monitoring_enabled ? <Pause size={15} /> : <Play size={15} />}<span>{channel.monitoring_enabled ? "Pause" : "Resume"}</span>
                  </button>
                  <button className="channel-action-button remove-channel-button" type="button" onClick={() => removeChannel(channel)} disabled={Boolean(busyAction)} aria-label={`Remove ${channel.title}`}>
                    {busy && busyAction.endsWith(":remove") ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />}<span>Remove</span>
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      )}

      <section className="latest-discoveries">
        <div className="panel-heading"><div><h3>Latest discovered videos</h3><p>Public video metadata only. A rights status is your declaration, not verified permission.</p></div></div>
        {summaryLoading ? (
          <div className="channels-message latest-loading" role="status"><LoaderCircle className="spin" size={17} /> Loading recent discoveries...</div>
        ) : summary?.latest_videos?.length ? (
          <div className="discovered-video-list">
            {summary.latest_videos.map((video) => (
              <article className="discovered-video-card" key={video.id}>
                {video.thumbnail_url ? <img src={video.thumbnail_url} alt="" loading="lazy" /> : <div className="video-thumbnail-fallback"><Youtube size={20} /></div>}
                <div className="discovered-video-details"><strong>{video.title}</strong><span>{video.channel_title} · {dateText(video.published_at)}</span><span className={`rights-status rights-${video.rights_status}`}>Your rights declaration: {video.rights_status}</span></div>
                <a className="channel-action-button youtube-video-link" href={video.video_url} target="_blank" rel="noreferrer"><ExternalLink size={15} /><span>Open on YouTube</span></a>
              </article>
            ))}
          </div>
        ) : summaryError ? (
          <div className="channels-message channels-error latest-loading" role="alert">Recent discoveries are unavailable.</div>
        ) : (
          <div className="channels-message latest-loading">No public videos have been discovered yet.</div>
        )}
      </section>

      {selectedChannel && (
        <div className="video-dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) closeVideos(); }}>
          <section className="video-dialog" role="dialog" aria-modal="true" aria-labelledby="channel-videos-title">
            <header className="video-dialog-heading">
              <div><h2 id="channel-videos-title">Videos from {selectedChannel.title}</h2><p>Metadata only. Rights labels are your own declarations, not independently verified proof or a licence.</p></div>
              <button className="icon-button" type="button" onClick={closeVideos} aria-label="Close videos"><X size={18} /></button>
            </header>
            {videosLoading ? (
              <div className="channels-message" role="status"><LoaderCircle className="spin" size={18} /> Loading videos…</div>
            ) : videosError ? (
              <div className="channels-message channels-error" role="alert">{videosError}</div>
            ) : videos.length === 0 ? (
              <div className="channels-message">No public videos were found for this channel.</div>
            ) : (
              <div className="discovered-video-list">
                {videos.map((video) => (
                  <article className="discovered-video-card" key={video.id || video.video_id}>
                    {video.thumbnail_url ? <img src={video.thumbnail_url} alt="" loading="lazy" /> : <div className="video-thumbnail-fallback"><Youtube size={20} /></div>}
                    <div className="discovered-video-details">
                      <strong>{video.title}</strong>
                      <span>{dateText(video.published_at)}</span>
                      <span className={`rights-status rights-${video.rights_status}`}>Rights: {video.rights_status}</span>
                      <label className="rights-select-label">
                        Your rights review
                        <select
                          value={video.rights_status}
                          onChange={(event) => updateRightsStatus(selectedChannel, video, event.target.value)}
                        >
                          <option value="unverified">Not reviewed</option>
                          <option value="owned">I own this content</option>
                          <option value="authorized">I have redistribution permission</option>
                          <option value="rejected">Do not use this video</option>
                        </select>
                      </label>
                    </div>
                    <a className="channel-action-button youtube-video-link" href={video.video_url || `https://www.youtube.com/watch?v=${video.video_id}`} target="_blank" rel="noreferrer" aria-label={`Open ${video.title} on YouTube`}>
                      <ExternalLink size={15} /><span>Open on YouTube</span>
                    </a>
                  </article>
                ))}
              </div>
            )}
          </section>
        </div>
      )}
    </section>
  );
}
