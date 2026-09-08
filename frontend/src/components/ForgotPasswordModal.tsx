import { useState } from "react";
import Modal from "./Modal";
import { requestPasswordReset } from "../api";

interface ForgotPasswordModalProps {
  initialEmail?: string;
  onClose: () => void;
}

export default function ForgotPasswordModal({ initialEmail = "", onClose }: ForgotPasswordModalProps) {
  const [email, setEmail] = useState(initialEmail);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setStatus(null);
    if (!email.trim()) {
      setError("Please enter your email.");
      return;
    }
    try {
      await requestPasswordReset(email.trim());
      setStatus("A reset token has been sent to your email address.");
    } catch (err) {
      setError((err as Error).message ?? "Failed to request reset.");
    }
  };

  return (
    <Modal title="Forgot Password" onClose={onClose}>
      <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
        <label>
          Email address:
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            style={{ width: "100%", padding: "6px 8px", marginTop: "4px" }}
          />
        </label>
        {error && <p style={{ color: "var(--red)", margin: 0 }}>{error}</p>}
        {status && <p style={{ color: "var(--green)", margin: 0 }}>{status}</p>}
        <div style={{ display: "flex", gap: "8px", justifyContent: "flex-end" }}>
          <button type="button" className="btn btn-outline" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary">
            Send Reset
          </button>
        </div>
      </form>
    </Modal>
  );
}
