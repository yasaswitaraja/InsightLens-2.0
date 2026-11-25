
import { useState } from "react";
import { Eye, EyeOff, LockKeyhole, Mail, ArrowRight } from "lucide-react";

export default function Auth({ apiFetch, onAuthenticated }) {
  const [mode, setMode] = useState("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(event) {
    event.preventDefault();
    setError("");

    if (mode === "register") {
      const passwordRules = [
        password.length >= 12,
        /[A-Z]/.test(password),
        /[a-z]/.test(password),
        /[0-9]/.test(password),
        /[^A-Za-z0-9]/.test(password),
      ];

      const commonPasswords = [
        "password123!", "password1234!", "password@123",
        "welcome123!", "admin123456!", "qwerty123456!",
        "iloveyou123!", "abc123456789!", "123456789abc!",
        "password@1234", "welcome@1234",
      ];

      const emailName = email.trim().split("@")[0].toLowerCase();

      if (!passwordRules.every(Boolean)) {
        setError("Please meet all password requirements.");
        return;
      }

      if (commonPasswords.includes(password.toLowerCase())) {
        setError("Please choose a less common password.");
        return;
      }

      if (emailName.length >= 4 && password.toLowerCase().includes(emailName)) {
        setError("Password must not contain your email username.");
        return;
      }
    }

    setLoading(true);

    try {
      const response = await apiFetch(
        mode === "login" ? "/api/auth/login" : "/api/auth/register",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            email: email.trim(),
            password,
          }),
        }
      );

      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(data.detail || "Authentication failed.");
      }

      if (!data.access_token || !data.user) {
        throw new Error("The server returned an invalid authentication response.");
      }

      onAuthenticated(data);
    } catch (err) {
      setError(err.message || "Could not connect to the server.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="auth-page">
      <section className="auth-card">
        <div className="auth-brand">
          <img src="/insightlens-logo.png" alt="InsightLens" className="brand-logo" />
        </div>

        <div className="auth-heading">
          <div className="eyebrow">YOUR RESEARCH SPACE</div>
          <h1>{mode === "login" ? "Welcome back." : "Create your account."}</h1>
          <p>
            {mode === "login"
              ? "Sign in to continue your research and learning."
              : "Create an account to start building your research workspace."}
          </p>
        </div>

        <form onSubmit={handleSubmit} className="auth-form">
          <label htmlFor="auth-email">Email address</label>
          <div className="auth-input">
            <Mail size={17} />
            <input
              id="auth-email"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="you@example.com"
              autoComplete="email"
              required
            />
          </div>

          <label htmlFor="auth-password">Password</label>
          <div className="auth-input">
            <LockKeyhole size={17} />
            <input
              id="auth-password"
              type={showPassword ? "text" : "password"}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder={mode === "register" ? "Create a strong password" : "Your password"}
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              minLength={mode === "register" ? 12 : 8}
              maxLength={128}
              required
            />
            <button
              type="button"
              className="auth-password-toggle"
              onClick={() => setShowPassword((value) => !value)}
              aria-label={showPassword ? "Hide password" : "Show password"}
            >
              {showPassword ? <EyeOff size={17} /> : <Eye size={17} />}
            </button>
          </div>

          {mode === "register" && (
            <div className="password-requirements">
              <p>Password requirements</p>
              {[
                [password.length >= 12, "At least 12 characters"],
                [/[A-Z]/.test(password), "One uppercase letter"],
                [/[a-z]/.test(password), "One lowercase letter"],
                [/[0-9]/.test(password), "One number"],
                [/[^A-Za-z0-9]/.test(password), "One special character"],
                [
                  password.length > 0 &&
                    !password.toLowerCase().includes(email.trim().split("@")[0].toLowerCase()) &&
                    email.trim().split("@")[0].length >= 4,
                  "Must not contain your email username",
                ],
              ].map(([passed, label], index) => (
                <div className={passed ? "password-rule passed" : "password-rule"} key={index}>
                  <span>{passed ? "?" : "?"}</span> {label}
                </div>
              ))}
            </div>
          )}

          {error && <div className="auth-error">{error}</div>}

          <button className="auth-submit" type="submit" disabled={loading}>
            {loading
              ? "Please wait..."
              : mode === "login"
                ? "Sign in"
                : "Create account"}
            {!loading && <ArrowRight size={17} />}
          </button>
        </form>

        <div className="auth-switch">
          {mode === "login" ? "Don't have an account?" : "Already have an account?"}
          <button
            type="button"
            onClick={() => {
              setMode(mode === "login" ? "register" : "login");
              setError("");
            }}
          >
            {mode === "login" ? "Create account" : "Sign in"}
          </button>
        </div>

        <div className="auth-footer">
          Your research workspace, organized in one place.
        </div>
      </section>
    </main>
  );
}
