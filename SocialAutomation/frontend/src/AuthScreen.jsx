import { useState } from "react";
import { ArrowRight, LoaderCircle, Play, ShieldCheck } from "lucide-react";
import { apiFetch, responseError } from "./api";

export default function AuthScreen({ onAuthenticated }) {
  const [mode, setMode] = useState("login");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [passwordConfirm, setPasswordConfirm] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const isRegistering = mode === "register";

  async function handleSubmit(event) {
    event.preventDefault();
    setError("");
    setLoading(true);
    try {
      const response = await apiFetch(`/api/auth/${isRegistering ? "register" : "login"}/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username,
          password,
          ...(isRegistering ? { email, password_confirm: passwordConfirm } : {}),
        }),
      });
      if (!response.ok) throw new Error(await responseError(response));
      const data = await response.json();
      onAuthenticated(data.user);
    } catch (requestError) {
      setError(requestError.message || "We couldn't sign you in. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="auth-shell">
      <section className="auth-card">
        <div className="auth-brand">
          <div className="brand-mark"><Play size={18} fill="currentColor" /></div>
          <div><strong>social<span>automation</span></strong><small>CONTENT OPERATIONS</small></div>
        </div>
        <div className="auth-heading">
          <div className="auth-icon"><ShieldCheck size={20} /></div>
          <span className="eyebrow">YOUR WORKSPACE</span>
          <h1>{isRegistering ? "Create your account" : "Welcome back"}</h1>
          <p>{isRegistering ? "Set up a secure account for your content workspace." : "Sign in to continue to your content workspace."}</p>
        </div>

        <form className="auth-form" onSubmit={handleSubmit}>
          <label>Username<input autoComplete="username" required value={username} onChange={(event) => setUsername(event.target.value)} /></label>
          {isRegistering && <label>Email address<input type="email" autoComplete="email" required value={email} onChange={(event) => setEmail(event.target.value)} /></label>}
          <label>Password<input type="password" autoComplete={isRegistering ? "new-password" : "current-password"} required value={password} onChange={(event) => setPassword(event.target.value)} /></label>
          {isRegistering && <label>Confirm password<input type="password" autoComplete="new-password" required value={passwordConfirm} onChange={(event) => setPasswordConfirm(event.target.value)} /></label>}
          {error && <div className="auth-error" role="alert">{error}</div>}
          <button className="primary-button auth-submit" type="submit" disabled={loading}>
            {loading ? <><LoaderCircle className="spin" size={16} /> {isRegistering ? "Creating account…" : "Signing in…"}</> : <>{isRegistering ? "Create account" : "Sign in"} <ArrowRight size={16} /></>}
          </button>
        </form>

        <div className="auth-switch">
          {isRegistering ? "Already have an account?" : "New to SocialAutomation?"}
          <button type="button" onClick={() => { setMode(isRegistering ? "login" : "register"); setError(""); }}>
            {isRegistering ? "Sign in" : "Create an account"}
          </button>
        </div>
        <div className="auth-security"><ShieldCheck size={14} /> Your session is protected with secure cookies.</div>
      </section>
    </main>
  );
}
