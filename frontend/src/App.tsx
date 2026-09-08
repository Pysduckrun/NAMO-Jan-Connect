import { FormEvent, useEffect, useState } from "react";
import NamoApp from "./components/NamoApp";
import HowItWorksPage from "./components/HowItWorksPage";
import AboutPage from "./components/AboutPage";
import GalleryPage from "./components/GalleryPage";
import PrivacyPage from "./components/PrivacyPage";
import AccessibilityPage from "./components/AccessibilityPage";
import ContactPage from "./components/ContactPage";
import ForgotPasswordModal from "./components/ForgotPasswordModal";
import AccessibilityBar from "./components/AccessibilityBar";
import { LanguageProvider, useLanguage } from "./context/LanguageContext";
import { apiFetch, readJson, getCookie, setCookie, eraseCookie } from "./api";
import { 
  Building2, 
  ShieldAlert, 
  Activity, 
  FileText, 
  ArrowLeft, 
  Heart, 
  Users2,
  ChevronDown,
  Lock
} from "lucide-react";

type Portal = "admin" | "department";
type Session = { access_token: string; role: string; name: string; department_category?: string | null };

const info: Record<string, { eyebrow: string; title: string; intro: string; points: string[] }> = {
  "/how-it-works": { eyebrow: "ONE CLEAR JOURNEY", title: "How it works", intro: "A complaint is routed instantly and remains traceable through every department action.", points: ["Describe the concern and pin its exact location", "Receive a public tracking ID", "Follow acknowledgement, progress, and resolution"] },
  "/about": { eyebrow: "PUBLIC SERVICE, MADE VISIBLE", title: "About us", intro: "NAMO Jan Connect gives public concerns a clear and accountable service trail.", points: ["Citizen-first reporting", "Department ownership", "Transparent outcomes"] },
  "/gallery": { eyebrow: "PROOF, NOT PROMISES", title: "Solved gallery", intro: "Verified resolution evidence is published without exposing citizen contact information.", points: ["Resolution photographs", "Department and location", "Linked public tracking records"] },
  "/privacy": { eyebrow: "PRIVACY BY DESIGN", title: "Privacy", intro: "Contact details are used only to process and update complaints.", points: ["No public email or phone display", "Role-scoped staff access", "Auditable status history"] },
  "/accessibility": { eyebrow: "ACCESS FOR EVERYONE", title: "Accessibility", intro: "The interface supports keyboard navigation, readable contrast, responsive layouts, and reduced motion.", points: ["Keyboard-friendly controls", "Light and dark themes", "Reduced-motion support"] },
  "/contact": { eyebrow: "WE ARE HERE TO HELP", title: "Contact", intro: "For support, include your tracking ID so the team can find the complaint quickly.", points: ["Complaint tracking support", "Privacy requests", "Accessibility feedback"] },
};

const departmentPaths: Record<string, { category: string; label: string }> = {
  "/civic-infra": { category: "civic_infra", label: "Civic & Infrastructure" },
  "/civil-department": { category: "civic_infra", label: "Civic & Infrastructure" },
  "/health-education": { category: "health_edu", label: "Health & Education" },
  "/law-order": { category: "law_order", label: "Law & Order" },
  "/transport": { category: "transport", label: "Transport & Public Services" },
  "/employment-welfare": { category: "employment_welfare", label: "Employment & Welfare" },
};

const PORTAL_OPTIONS = [
  { value: "admin", label: "Central Administration", hindi: "केंद्रीय प्रशासन", path: "/admin", isDept: false },
  { value: "civic_infra", label: "Civic & Infrastructure", hindi: "नागरिक एवं अवसंरचना", path: "/civic-infra", isDept: true },
  { value: "health_edu", label: "Health & Education", hindi: "स्वास्थ्य एवं शिक्षा", path: "/health-education", isDept: true },
  { value: "law_order", label: "Law & Order", hindi: "कानून और व्यवस्था", path: "/law-order", isDept: true },
  { value: "transport", label: "Transport & Public Services", hindi: "परिवहन एवं जन सेवाएं", path: "/transport", isDept: true },
  { value: "employment_welfare", label: "Employment & Welfare", hindi: "रोजगार एवं कल्याण", path: "/employment-welfare", isDept: true },
];

function StaffLogin(props: { portal?: Portal; departmentCategory?: string; departmentLabel?: string; children?: React.ReactNode }) {
  return (
    <LanguageProvider>
      <StaffLoginInner {...props} />
    </LanguageProvider>
  );
}

function StaffLoginInner({ portal: initialPortal = "department", departmentCategory: initialCategory, departmentLabel: initialLabel, children }: { portal?: Portal; departmentCategory?: string; departmentLabel?: string; children?: React.ReactNode }) {
  const [session, setSession] = useState<Session | null>(() => {
    try {
      const fromCookie = getCookie("njc_staff_session");
      if (fromCookie) return JSON.parse(fromCookie);
      return JSON.parse(localStorage.getItem("njc_staff_session") || "null");
    } catch {
      return null;
    }
  });

  // Selected portal in dropdown: "admin" or category key ("civic_infra", etc.)
  const defaultSelectedKey = initialPortal === "admin" 
    ? "admin" 
    : (initialCategory || "civic_infra");

  const [selectedPortalKey, setSelectedPortalKey] = useState<string>(defaultSelectedKey);
  const [loginIdentifier, setLoginIdentifier] = useState("");
  const [loginPassword, setLoginPassword] = useState("");

  // Clear any legacy saved credentials from cookies on mount to guarantee fields stay blank on reload
  useEffect(() => {
    eraseCookie("njc_saved_email");
    eraseCookie("njc_saved_password");
  }, []);

  const [error, setError] = useState(""); 
  const [busy, setBusy] = useState(false);
  const { t, language } = useLanguage();

  const [showForgot, setShowForgot] = useState(false);

  // Active target portal definition based on dropdown
  const currentOption = PORTAL_OPTIONS.find((opt) => opt.value === selectedPortalKey) || PORTAL_OPTIONS[0];
  const targetPortal: Portal = currentOption.value === "admin" ? "admin" : "department";
  const targetCategory = currentOption.value === "admin" ? undefined : currentOption.value;
  const targetLabel = currentOption.label;

  useEffect(() => {
    if (session) {
      const str = JSON.stringify(session);
      localStorage.setItem("njc_staff_session", str);
      setCookie("njc_staff_session", str, 30);
    }
  }, [session]);

  async function login(event: FormEvent<HTMLFormElement>) { 
    event.preventDefault(); 
    setBusy(true); 
    setError(""); 
    const data = new FormData(event.currentTarget);
    const identifierVal = String(data.get("identifier") || "");
    const passwordVal = String(data.get("password") || "");
    try { 
      const response = await apiFetch("/api/auth/login", { 
        method: "POST", 
        headers: { "content-type": "application/json" }, 
        body: JSON.stringify({ identifier: identifierVal, password: passwordVal }) 
      }); 
      const result = await readJson<Session & { detail?: string }>(response); 
      if (!response.ok) throw new Error(result.detail || "Sign-in failed"); 

      if (targetPortal === "admin" && result.role !== "admin") {
        throw new Error("Administrator credentials required for Central Administration portal.");
      }
      if (targetPortal === "department") { 
        if (!["department_staff", "admin"].includes(result.role)) {
          throw new Error("Department officer credentials required.");
        }
        if (result.role !== "admin" && targetCategory && result.department_category !== targetCategory) { 
          throw new Error(`This account is assigned to another department, not ${targetLabel}.`); 
        } 
      } 
      const sessionStr = JSON.stringify(result);
      localStorage.setItem("njc_staff_session", sessionStr);
      setCookie("njc_staff_session", sessionStr, 30);
      setSession(result); 

      // If we are on /dashboard or on a mismatching URL, navigate to the portal route
      const targetUrl = currentOption.path;
      if (window.location.pathname !== targetUrl && window.location.pathname === "/dashboard") {
        window.history.pushState({}, "", targetUrl);
      }
    } catch (caught) { 
      setError(caught instanceof Error ? caught.message : "Sign-in failed"); 
    } finally { 
      setBusy(false); 
    } 
  }

  function handleSignOut() {
    localStorage.removeItem("njc_staff_session");
    eraseCookie("njc_staff_session");
    eraseCookie("njc_access_token");
    setSession(null);
  }

  const isSessionInvalid = !session ? true : (
    (targetPortal === "admin" && session.role !== "admin") ||
    (targetPortal === "department" && 
     !["department_staff", "admin"].includes(session.role)) ||
    (targetPortal === "department" && 
     session.role !== "admin" && 
     targetCategory && 
     session.department_category !== targetCategory)
  );

  useEffect(() => {
    if (session && isSessionInvalid) {
      handleSignOut();
    }
  }, [session, isSessionInvalid]);

  if (session && !isSessionInvalid) {
    if (children) {
      return (
        <>
          {children}
          <button className="staff-logout" onClick={handleSignOut}>
            Sign out
          </button>
        </>
      );
    }
    // Render the portal app directly based on selection
    return (
      <>
        <NamoApp initialPortal={targetPortal} />
        <button className="staff-logout" onClick={handleSignOut}>
          Sign out
        </button>
      </>
    );
  }
  
  const emailPlaceholder = targetPortal === "admin" ? "admin@namo.gov.in" : "officer@namo.gov.in";

  return (
    <div className="split-login-viewport">
      <div className="tricolor-stripe" aria-hidden="true"><span /><span /><span /></div>
      <AccessibilityBar />
      
      <main className="split-login-main">
        <div className="split-login-container">
          
          {/* Left Hero Pane: Indian Flag Visual & National Branding */}
          <section className="split-login-hero" aria-label="National Portal Branding">
            <div className="split-hero-bg-overlay" />
            <div className="split-hero-content">
              <div className="split-emblem-wrap">
                <img src="/emblem.png" alt="State Emblem of India" className="split-emblem-img" />
              </div>
              
              <div className="split-hero-titles">
                <span className="split-hero-slogan">सत्यमेव जयते | SATYAMEVA JAYATE</span>
                <h2 className="split-hero-h2">Government of India</h2>
                <h3 className="split-hero-h3">भारत सरकार • राष्ट्रीय शिकायत निवारण</h3>
                <p className="split-hero-sub">
                  NAMO Jan Connect — Unified Governance, Rapid Redressal &amp; SLA Tracking Portal.
                </p>
              </div>

              <div className="split-hero-badges">
                <span className="split-pill-badge">
                  <Lock size={12} style={{ display: "inline", verticalAlign: "middle", marginRight: 4 }} />
                  Restricted Official Access
                </span>
                <span className="split-pill-badge-outline">
                  256-Bit Encrypted
                </span>
              </div>
            </div>
          </section>

          {/* Right Form Pane: Clean Full-Height Sign-In Form */}
          <section className="split-login-form-pane" aria-label="Officer Sign-in Form">
            <div className="split-form-box">
              
              {/* Header inside form */}
              <div className="split-form-header">
                <div className="split-mobile-flag-banner" aria-hidden="true" />
                <div className="split-form-badge">
                  {t("login.secure_portal")}
                </div>
                <h1 className="split-form-title">Officer Sign In</h1>
                <p className="split-form-sub">
                  Select your assigned department or central administration to continue to your dashboard.
                </p>
              </div>

              <form className="split-login-form" onSubmit={login}>
                
                {/* Dropdown: Choose Portal / Department */}
                <label className="form-field-label">
                  <span className="form-label-row">
                    <span>Select Portal / Department</span>
                    <span className="hindi-hint">पोर्टल / विभाग चुनें</span>
                  </span>
                  <div className="split-select-wrapper">
                    <select
                      value={selectedPortalKey}
                      onChange={(e) => setSelectedPortalKey(e.target.value)}
                      className="split-select-input"
                      aria-label="Select Portal or Department"
                    >
                      {PORTAL_OPTIONS.map((opt) => (
                        <option key={opt.value} value={opt.value}>
                          {opt.label} ({opt.hindi})
                        </option>
                      ))}
                    </select>
                    <ChevronDown size={16} className="split-select-chevron" aria-hidden="true" />
                  </div>
                </label>

                {/* Email Field */}
                <label className="form-field-label">
                  <span className="form-label-row">
                    <span>{targetPortal === "admin" ? t("login.admin_email") : t("login.email")}</span>
                    <span className="hindi-hint">ईमेल पता</span>
                  </span>
                  <input
                    name="identifier" 
                    type="email" 
                    required 
                    value={loginIdentifier}
                    onChange={(e) => setLoginIdentifier(e.target.value)}
                    autoComplete="off" 
                    placeholder={emailPlaceholder}
                    className="split-text-input"
                  />
                </label>

                {/* Password Field */}
                <label className="form-field-label">
                  <span className="form-label-row">
                    <span>{t("login.password")}</span>
                    <span className="hindi-hint">पासवर्ड</span>
                  </span>
                  <input
                    name="password" 
                    type="password" 
                    required 
                    value={loginPassword}
                    onChange={(e) => setLoginPassword(e.target.value)}
                    autoComplete="new-password" 
                    placeholder="••••••••••••"
                    className="split-text-input"
                  />
                </label>

                {/* Forgot Password Link */}
                <div className="login-forgot-row">
                  <button
                    type="button"
                    className="forgot-password-link"
                    onClick={() => setShowForgot(true)}
                  >
                    Forgot password? / पासवर्ड भूल गए?
                  </button>
                </div>

                {error && <p className="form-error-banner" role="alert">{error}</p>}

                {/* Submit Button */}
                <button 
                  type="submit" 
                  className="btn btn-primary split-submit-btn" 
                  disabled={busy}
                >
                  {busy ? t("login.signing_in") : `${t("login.signin")} to ${currentOption.label}`}
                </button>
              </form>

              {showForgot && (
                <ForgotPasswordModal 
                  initialEmail={loginIdentifier} 
                  onClose={() => setShowForgot(false)} 
                />
              )}
              
              <div className="split-footer-actions">
                <a href="/" className="return-home-link">
                  <ArrowLeft size={14} style={{ display: "inline", verticalAlign: "middle", marginRight: 4 }} />
                  {t("login.return")}
                </a>
              </div>
            </div>
          </section>

        </div>
      </main>
    </div>
  );
}

function InfoPage({ data }: { data: (typeof info)[string] }) { return <div className="info-shell"><header className="info-header"><a className="brand" href="/"><span><b>NAMO</b><small>JAN CONNECT</small></span></a></header><main><section className="info-hero"><p className="eyebrow">{data.eyebrow}</p><h1>{data.title}</h1><p>{data.intro}</p></section><section className="info-section principle-grid">{data.points.map((point, index) => <article key={point}><span>0{index + 1}</span><h3>{point}</h3><p>Designed to keep the service process clear, accessible, and accountable.</p></article>)}</section></main></div>; }

function DashboardHub() {
  return (
    <main className="dashboard-hub">
      <div className="dashboard-hub-inner">
        <p className="eyebrow">PORTAL ACCESS DIRECTORY</p>
        <h1 className="launcher-title">NAMO Jan Connect Hub</h1>
        <p className="launcher-subtitle">Access municipal dashboards, citizen services tracking, and admin controls.</p>
        
        <div className="launcher-grid">
          <a href="/citizen" className="flex-col-card">
            <div className="card-icon-container bg-blue">
              <Users2 size={20} />
            </div>
            <div className="card-content">
              <b>Citizen Dashboard</b>
              <small>View filed complaints, resolution statuses, and live tracking timelines.</small>
            </div>
          </a>

          <a href="/civic-infra" className="flex-col-card">
            <div className="card-icon-container bg-amber">
              <Building2 size={20} />
            </div>
            <div className="card-content">
              <b>Civic &amp; Infrastructure</b>
              <small>Resolve civic issues, manage water, streetlights, and roads.</small>
            </div>
          </a>

          <a href="/health-education" className="flex-col-card">
            <div className="card-icon-container bg-teal">
              <Heart size={20} />
            </div>
            <div className="card-content">
              <b>Health &amp; Education</b>
              <small>Manage clinic, hospital, and school-related issues.</small>
            </div>
          </a>

          <a href="/law-order" className="flex-col-card">
            <div className="card-icon-container bg-coral">
              <ShieldAlert size={20} />
            </div>
            <div className="card-content">
              <b>Law &amp; Order</b>
              <small>Review public safety, local policing, and order complaints.</small>
            </div>
          </a>

          <a href="/transport" className="flex-col-card">
            <div className="card-icon-container bg-sky">
              <Building2 size={20} />
            </div>
            <div className="card-content">
              <b>Transport &amp; Public Services</b>
              <small>Manage transport permits, PWD roads, and transit issues.</small>
            </div>
          </a>

          <a href="/employment-welfare" className="flex-col-card">
            <div className="card-icon-container bg-green">
              <FileText size={20} />
            </div>
            <div className="card-content">
              <b>Employment &amp; Welfare</b>
              <small>Review social pensions, jobs, and social support concerns.</small>
            </div>
          </a>

          <a href="/admin" className="portal-link-admin flex-col-card">
            <div className="card-icon-container bg-navy-accent">
              <Activity size={20} />
            </div>
            <div className="card-content">
              <b>Administrator Dashboard</b>
              <small>Full oversight, department routing, and live SLA analytics.</small>
            </div>
          </a>
        </div>
        
        <a href="/" className="launcher-back-link">
          <ArrowLeft size={14} /> Return to public site
        </a>
      </div>
    </main>
  );
}

export default function App() {
  const path = window.location.pathname.replace(/\/$/, "") || "/";
  // Apply saved theme before first paint (synchronous, no flash)
  let t = localStorage.getItem("njc-theme") || "light";
  if (t === "contrast") t = "light";
  document.documentElement.dataset.theme = t;
  document.documentElement.style.colorScheme = t === "dark" ? "dark" : "light";
  const f = localStorage.getItem("njc-font") || "md";
  document.documentElement.dataset.fontsize = f;
  if (path === "/admin") return <StaffLogin portal="admin"><NamoApp initialPortal="admin" /></StaffLogin>;
  if (path === "/citizen") return <NamoApp initialPortal="citizen" />;
  if (path === "/dashboard") return <StaffLogin />;
  if (path === "/hub") return <DashboardHub />;
  if (path === "/how-it-works") return <HowItWorksPage />;
  if (path === "/about") return <AboutPage />;
  if (path === "/gallery") return <GalleryPage />;
  if (path === "/privacy") return <PrivacyPage />;
  if (path === "/accessibility") return <AccessibilityPage />;
  if (path === "/contact") return <ContactPage />;
  const dept = departmentPaths[path];
  if (dept) return <StaffLogin portal="department" departmentCategory={dept.category} departmentLabel={dept.label}><NamoApp initialPortal="department" /></StaffLogin>;
  if (info[path]) return <InfoPage data={info[path]} />;
  return <NamoApp />;
}
