import React, { useEffect, useState } from "react";

interface ImageUploadPreviewProps {
  files: File[];
  onRemove?: (index: number) => void;
  maxFiles?: number;
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

export default function ImageUploadPreview({
  files,
  onRemove,
  maxFiles = 4,
}: ImageUploadPreviewProps) {
  const [previews, setPreviews] = useState<{ file: File; url: string }[]>([]);
  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null);

  useEffect(() => {
    const items = files.map((file) => ({
      file,
      url: URL.createObjectURL(file),
    }));
    setPreviews(items);

    return () => {
      items.forEach((item) => URL.revokeObjectURL(item.url));
    };
  }, [files]);

  useEffect(() => {
    if (lightboxIndex === null) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setLightboxIndex(null);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [lightboxIndex]);

  if (files.length === 0) {
    return null;
  }

  return (
    <div className="upload-preview-container">
      <div className="upload-preview-header">
        <span className="upload-preview-count">
          {files.length} of {maxFiles} photo{files.length > 1 ? "s" : ""} selected
        </span>
        <small className="upload-preview-tip">Click image to enlarge</small>
      </div>

      <div className="upload-preview-grid">
        {previews.map((item, index) => (
          <div key={`${item.file.name}-${index}`} className="upload-preview-item">
            <div
              className="upload-preview-thumb-wrap"
              onClick={() => setLightboxIndex(index)}
              title={`Click to preview ${item.file.name}`}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  setLightboxIndex(index);
                }
              }}
            >
              <img
                src={item.url}
                alt={item.file.name}
                className="upload-preview-thumb"
              />
              <span className="upload-preview-zoom-icon" aria-hidden="true">
                🔍
              </span>
            </div>

            {onRemove && (
              <button
                type="button"
                className="upload-preview-remove"
                onClick={(e) => {
                  e.stopPropagation();
                  onRemove(index);
                }}
                title={`Remove ${item.file.name}`}
                aria-label={`Remove photo ${index + 1}: ${item.file.name}`}
              >
                ✕
              </button>
            )}

            <div className="upload-preview-meta">
              <span className="upload-preview-name" title={item.file.name}>
                {item.file.name}
              </span>
              <span className="upload-preview-size">
                {formatFileSize(item.file.size)}
              </span>
            </div>
          </div>
        ))}
      </div>

      {lightboxIndex !== null && previews[lightboxIndex] && (
        <div
          className="upload-preview-lightbox"
          onClick={() => setLightboxIndex(null)}
          role="dialog"
          aria-modal="true"
          aria-label="Photo Preview"
        >
          <div
            className="upload-preview-lightbox-content"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              className="upload-preview-lightbox-close"
              onClick={() => setLightboxIndex(null)}
              aria-label="Close preview"
            >
              ✕
            </button>
            <img
              src={previews[lightboxIndex].url}
              alt={previews[lightboxIndex].file.name}
              className="upload-preview-lightbox-img"
            />
            <div className="upload-preview-lightbox-caption">
              <span>{previews[lightboxIndex].file.name}</span>
              <span>{formatFileSize(previews[lightboxIndex].file.size)}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
