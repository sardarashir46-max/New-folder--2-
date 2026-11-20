import React from 'react';
import { useCallback, useEffect, useState } from "react";
import AuthScreen from "./AuthScreen";
import FacebookPagesPanel from "./FacebookPagesPanel";
import MediaLibraryPanel from "./MediaLibraryPanel";
import PublishingCalendarPanel from "./PublishingCalendarPanel";
import PublishingHistoryPanel from "./PublishingHistoryPanel";
import YouTubeChannelsPanel from "./YouTubeChannelsPanel";
import { apiFetch, responseError } from "./api";
import {
  Activity, ArrowDownRight, ArrowUpRight, Bell, CalendarDays,
  CheckCircle2, ChevronDown, CircleHelp, Clock3, Facebook,
  FileVideo2, LayoutDashboard, Link2, Menu, MoreHorizontal,
  Play, Plus, Search, Settings2, ShieldCheck, Sparkles,
  Video, Youtube, X
} from "lucide-react";

const navItems = [
  { label: "Overview", icon: LayoutDashboard },
  { label: "YouTube channels", icon: Youtube },
  { label: "Facebook Pages", icon: Facebook },
  { label: "Media library", icon: FileVideo2 },
  { label: "Publishing calendar", icon: CalendarDays },
  { label: "Activity logs", icon: Activity },
];

function StatCard({ label, value, change, icon: Icon, tone }) {
  return (
    <article className="stat-card">
      <div className="stat-top"><span>{label}</span><span className={`stat-icon ${tone}`}><Icon size={18} /></span></div>
      <div className="stat-value">{value}</div>
      <div className="stat-foot"><span className="trend"><ArrowUpRight size={14} /> {change}</span></div>
    </article>
  );
}

function Dashboard({ user, onLogout, onSessionExpired, logoutLoading, logoutError, facebookCallbackStatus }) {
  const [active, setActive] = useState(() => facebookCallbackStatus ? "Facebook Pages" : "Overview");
  const [mobileOpen, setMobileOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [notice, setNotice] = useState("");
  const [summary, setSummary] = useState(null);
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [summaryError, setSummaryError] = useState("");
  const title = active === "Overview" ? "Command center" : active;
  const descriptions = {
    "Overview": "Your content operations, all in one place.",
    "YouTube channels": "Connect and organize the channels you manage.",
    "Facebook Pages": "Manage the Pages authorized for publishing.",
    "Media library": "Organize original videos and licensed media.",
    "Publishing calendar": "Plan your content across channels and Pages.",
    "Activity logs": "Review publishing events and system activity."
  };

  const showNotice = (message) => {
    setNotice(message);
    window.setTimeout(() => setNotice(""), 2800);
  };

  useEffect(() => {
    const controller = new AbortController();
    async function loadSummary() {
      try {
        const response = await apiFetch("/api/dashboard/summary/", { signal: controller.signal });
        if (response.status === 401 || response.status === 403) onSessionExpired();
        if (!response.ok) throw new Error(await responseError(response));
        setSummary(await response.json());
      } catch (error) {
        if (error.name !== "AbortError") setSummaryError(error.message || "Dashboard totals are unavailable.");
      } finally {
        if (!controller.signal.aborted) setSummaryLoading(false);
      }
    }
    loadSummary();
    return () => controller.abort();
  }, [onSessionExpired]);

  return (
    <div className="app-shell">
      {mobileOpen && <button className="mobile-scrim" onClick={() => setMobileOpen(false)} aria-label="Close navigation" />}
      <aside className={`sidebar ${mobileOpen ? "sidebar-open" : ""}`}>
        <div className="brand">
          <div className="brand-mark"><Play size={18} fill="currentColor" /></div>
          <div><strong>social<span>automation</span></strong><small>CONTENT OPERATIONS</small></div>
          <button className="icon-button mobile-close" onClick={() => setMobileOpen(false)} aria-label="Close menu"><X size={18}/></button>
        </div>

        <div className="workspace-switch">
          <div className="workspace-avatar">SA</div>
          <div className="workspace-copy"><strong>My workspace</strong><span>Personal workspace</span></div>
          <ChevronDown size={15} className="muted" />
        </div>

        <div className="nav-label">WORKSPACE</div>
        <nav className="main-nav">
          {navItems.map(({ label, icon: Icon }) => (
            <button key={label} className={`nav-item ${active === label ? "active" : ""}`} onClick={() => { setActive(label); setMobileOpen(false); }}>
              <Icon size={18} strokeWidth={1.8} /><span>{label}</span>
              {label === "Activity logs" && <i className="nav-dot" />}
            </button>
          ))}
        </nav>

        <div className="sidebar-bottom">
          <div className="usage-card">
            <div className="usage-heading"><Sparkles size={15}/><strong>Getting started</strong></div>
            <p>Connect your accounts to start building your publishing workflow.</p>
            <div className="progress-track"><span /></div>
          <small>{summaryLoading ? "Checking connected accounts…" : `${Number(Boolean(summary?.youtube_connected)) + Number(Boolean(summary?.facebook_connected))} of 2 platforms connected`}</small>
          </div>
          <button className="nav-item" onClick={() => showNotice("Settings will be available in a later phase.")}><Settings2 size={18}/><span>Settings</span></button>
          <button className="nav-item" onClick={() => showNotice("Help center is coming soon.")}><CircleHelp size={18}/><span>Help & support</span></button>
          <div className="profile-row"><div className="profile-avatar">{user.username.slice(0, 1).toUpperCase()}</div><div className="profile-copy"><strong>{user.username}</strong><span>{user.email || "Workspace member"}</span></div><button className="logout-button" onClick={onLogout} disabled={logoutLoading}>{logoutLoading ? "Signing out…" : "Log out"}</button></div>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div className="topbar-left">
            <button className="icon-button mobile-menu" onClick={() => setMobileOpen(true)} aria-label="Open navigation"><Menu size={20}/></button>
            <div className="breadcrumb">Workspace <span>/</span> <strong>{active}</strong></div>
          </div>
          <div className="topbar-actions">
            <label className="search-box"><Search size={16}/><input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search anything..." /></label>
            <button className="icon-button notification-button" onClick={() => showNotice("You're all caught up.")} aria-label="Notifications"><Bell size={18}/><i/></button>
            <div className="top-avatar">{user.username.slice(0, 1).toUpperCase()}</div>
          </div>
        </header>

        <div className="page-content">
          <section className="page-heading">
            <div><div className="eyebrow"><span className="live-dot"/> WORKSPACE OVERVIEW</div><h1>{title}</h1><p>{descriptions[active]}</p></div>
            {!['YouTube channels', 'Facebook Pages'].includes(active) && <button className="primary-button" onClick={() => showNotice("Connect an account in the Accounts setup phase.")}><Plus size={17}/> Connect account</button>}
          </section>

          {active === "Overview" ? (
            <>
              <section className="welcome-banner">
                <div className="welcome-copy"><div className="welcome-tag"><Sparkles size={13}/> YOUR CONTENT, IN SYNC</div><h2>One workspace.<br/><span>Every channel.</span></h2><p>Bring your publishing workflow together. Connect your accounts, organize media, and plan what goes live next.</p><button className="banner-button" onClick={() => setActive("YouTube channels")}>Set up your workspace <ArrowUpRight size={16}/></button></div>
                <div className="banner-art" aria-hidden="true"><div className="orbit orbit-one"/><div className="orbit orbit-two"/><div className="orbit-core"><Play size={28} fill="currentColor"/></div><div className="float-icon youtube-float"><Youtube size={23}/></div><div className="float-icon facebook-float"><Facebook size={22}/></div><div className="float-icon video-float"><Video size={21}/></div><div className="orbit-star star-one">✦</div><div className="orbit-star star-two">✧</div></div>
              </section>

              <section className="stats-grid">
                <StatCard label="YouTube channels" value={summary?.youtube_channels ?? (summaryLoading ? "…" : "0")} change="In your workspace" icon={Youtube} tone="red"/>
                <StatCard label="Selected Facebook Pages" value={summary?.facebook_pages ?? (summaryLoading ? "…" : "0")} change="Ready for scheduling" icon={Facebook} tone="blue"/>
                <StatCard label="Scheduled posts" value={summary?.scheduled_posts ?? (summaryLoading ? "…" : "0")} change="Pending or processing" icon={CalendarDays} tone="violet"/>
                <StatCard label="Published this month" value={summary?.published_this_month ?? (summaryLoading ? "…" : "0")} change="Confirmed by Meta" icon={CheckCircle2} tone="green"/>
              </section>
              {summaryError && <div className="library-message error" role="alert">{summaryError}</div>}

              <section className="content-grid">
                <article className="panel upcoming-panel">
                  <div className="panel-heading"><div><h3>Upcoming publishing</h3><p>Your next scheduled content will appear here.</p></div><button className="text-button" onClick={() => setActive("Publishing calendar")}>View calendar <ArrowUpRight size={15}/></button></div>
                  {summaryLoading ? <div className="empty-state"><div className="empty-icon"><Clock3 size={23}/></div><strong>Loading your schedule…</strong></div> : summary?.upcoming_posts?.length ? <div className="overview-upcoming-list">{summary.upcoming_posts.map(post => <div className="overview-upcoming-row" key={post.id}><div><strong>{post.title}</strong><span>{post.pages.join(", ") || "No Page targets"}</span></div><time>{new Date(post.scheduled_for).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</time></div>)}<button className="subtle-button" onClick={() => setActive("Publishing calendar")}>Open calendar</button></div> : <div className="empty-state"><div className="empty-icon"><CalendarDays size={23}/></div><strong>Your calendar is clear</strong><span>Approved media and selected Pages are needed to schedule content.</span><button className="subtle-button" onClick={() => setActive("Publishing calendar")}>Open calendar</button></div>}
                </article>
                <article className="panel setup-panel">
                  <div className="panel-heading"><div><h3>Workspace setup</h3><p>Two quick steps to get moving.</p></div><span className="setup-count">{summaryLoading ? "…/2" : `${Number(Boolean(summary?.youtube_connected)) + Number(Boolean(summary?.facebook_connected))}/2`}</span></div>
                  <div className="setup-step"><div className="step-icon red-soft"><Youtube size={18}/></div><div><strong>Connect YouTube</strong><span>Bring in channel details and video metadata.</span></div><button onClick={() => setActive("YouTube channels")} aria-label="Connect YouTube"><ArrowUpRight size={17}/></button></div>
                  <div className="setup-step"><div className="step-icon blue-soft"><Facebook size={18}/></div><div><strong>Connect Facebook</strong><span>Choose the Pages you are authorized to manage.</span></div><button onClick={() => setActive("Facebook Pages")} aria-label="Connect Facebook"><ArrowUpRight size={17}/></button></div>
                  <div className="secure-note"><ShieldCheck size={15}/> Account connections will use official authorization flows.</div>
                </article>
              </section>

              <section className="panel activity-panel">
                <div className="panel-heading"><div><h3>Recent activity</h3><p>A live record of your workspace actions.</p></div><button className="text-button" onClick={() => setActive("Activity logs")}>View all <ArrowUpRight size={15}/></button></div>
                <div className="activity-empty"><Clock3 size={17}/> No activity recorded yet.</div>
              </section>
            </>
          ) : active === "YouTube channels" ? (
            <YouTubeChannelsPanel onSessionExpired={onSessionExpired} onNotice={showNotice} />
          ) : active === "Facebook Pages" ? (
            <FacebookPagesPanel
              onSessionExpired={onSessionExpired}
              onNotice={showNotice}
              callbackStatus={facebookCallbackStatus}
            />
          ) : active === "Media library" ? (
            <MediaLibraryPanel onSessionExpired={onSessionExpired} />
          ) : active === "Publishing calendar" ? (
            <PublishingCalendarPanel onSessionExpired={onSessionExpired} />
          ) : active === "Activity logs" ? (
            <PublishingHistoryPanel onSessionExpired={onSessionExpired} />
          ) : (
            <section className="panel section-placeholder">
              <div className="placeholder-icon">{active === "YouTube channels" ? <Youtube/> : active === "Facebook Pages" ? <Facebook/> : active === "Media library" ? <FileVideo2/> : active === "Publishing calendar" ? <CalendarDays/> : <Activity/>}</div>
              <h2>{active}</h2>
              <p>{descriptions[active]} This section is scaffolded and will be connected to the backend in the next development steps.</p>
              <button className="primary-button" onClick={() => showNotice("This feature is planned for the next milestone.")}><Plus size={16}/> Get started</button>
            </section>
          )}
          <footer className="footer"><span>© 2026 SocialAutomation</span><span><span className="footer-status"/> Workspace API connected <span className="footer-divider">·</span> Milestone 3 foundation</span></footer>
        </div>
      </main>
      {notice && <div className="toast"><CheckCircle2 size={17}/>{notice}</div>}
      {logoutError && <div className="toast toast-error" role="alert">{logoutError}</div>}
    </div>
  );
}

function App() {
  const [user, setUser] = useState(null);
  const [checkingSession, setCheckingSession] = useState(true);
  const [sessionError, setSessionError] = useState("");
  const [logoutLoading, setLogoutLoading] = useState(false);
  const [logoutError, setLogoutError] = useState("");
  const [facebookCallbackStatus] = useState(() => new URLSearchParams(window.location.search).get("facebook"));
  const handleSessionExpired = useCallback(() => setUser(null), []);

  async function checkSession(signal) {
    setCheckingSession(true);
    setSessionError("");
    try {
      const csrfResponse = await apiFetch("/api/auth/csrf/", { signal });
      if (!csrfResponse.ok) throw new Error("The authentication service is unavailable.");
      const response = await apiFetch("/api/auth/me/", { signal });
      if (response.status === 401 || response.status === 403) {
        setUser(null);
      } else if (!response.ok) {
        throw new Error(await responseError(response));
      } else {
        setUser((await response.json()).user);
      }
    } catch (error) {
      if (error.name !== "AbortError") {
        setSessionError(error.message || "The authentication service is unavailable.");
      }
    } finally {
      if (!signal?.aborted) setCheckingSession(false);
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    checkSession(controller.signal);
    return () => controller.abort();
  }, []);

  async function handleLogout() {
    setLogoutLoading(true);
    setLogoutError("");
    try {
      const response = await apiFetch("/api/auth/logout/", { method: "POST" });
      if (!response.ok) throw new Error(await responseError(response));
      setUser(null);
    } catch (error) {
      setLogoutError(error.message || "Sign out failed. Please retry.");
    } finally {
      setLogoutLoading(false);
    }
  }

  if (checkingSession) {
    return <main className="auth-shell"><div className="auth-loading"><span className="live-dot" /> Checking your session…</div></main>;
  }
  if (sessionError) {
    return <main className="auth-shell"><div className="auth-card auth-unavailable"><h1>Can't reach your workspace</h1><p>{sessionError}</p><button className="primary-button" onClick={() => checkSession()}>Try again</button></div></main>;
  }
  return user ? <Dashboard user={user} onLogout={handleLogout} onSessionExpired={handleSessionExpired} logoutLoading={logoutLoading} logoutError={logoutError} facebookCallbackStatus={facebookCallbackStatus} /> : <AuthScreen onAuthenticated={setUser} />;
}

export default App;
