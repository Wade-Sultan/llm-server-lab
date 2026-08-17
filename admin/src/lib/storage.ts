import { randomUUID } from 'crypto';
import { Storage } from '@google-cloud/storage';

// Server-only. Credentials come from Application Default Credentials: the
// palladium-admin GSA via Workload Identity in GKE, or `gcloud auth
// application-default login` locally. Never import this from a client
// component — it would pull the GCS SDK into the browser bundle.

const BUCKET = process.env.BLOG_MEDIA_BUCKET;

// Images are served straight from the public bucket. Set BLOG_MEDIA_BASE_URL if
// a CDN or custom domain ever fronts it; otherwise use the default GCS host.
const BASE_URL = process.env.BLOG_MEDIA_BASE_URL;

export const ALLOWED_IMAGE_TYPES = [
  'image/jpeg',
  'image/png',
  'image/webp',
  'image/gif',
  'image/avif',
] as const;

export const MAX_IMAGE_BYTES = 10 * 1024 * 1024; // 10 MB

const EXTENSIONS: Record<string, string> = {
  'image/jpeg': 'jpg',
  'image/png': 'png',
  'image/webp': 'webp',
  'image/gif': 'gif',
  'image/avif': 'avif',
};

let storage: Storage | null = null;

function getStorage(): Storage {
  if (!storage) storage = new Storage();
  return storage;
}

export class UploadError extends Error {}

/**
 * Upload a blog image to GCS and return its public URL.
 *
 * Object names are randomised rather than derived from the original filename:
 * it sidesteps path traversal and collisions, and keeps the (public) URL from
 * leaking whatever the file was called on my laptop.
 */
export async function uploadBlogImage(file: File): Promise<string> {
  if (!BUCKET) {
    throw new UploadError('BLOG_MEDIA_BUCKET is not configured on this server');
  }

  const contentType = file.type;
  if (!ALLOWED_IMAGE_TYPES.includes(contentType as (typeof ALLOWED_IMAGE_TYPES)[number])) {
    throw new UploadError(`Unsupported image type: ${contentType || 'unknown'}`);
  }
  if (file.size > MAX_IMAGE_BYTES) {
    throw new UploadError(
      `Image is ${(file.size / 1024 / 1024).toFixed(1)} MB; the limit is ${MAX_IMAGE_BYTES / 1024 / 1024} MB`
    );
  }

  const now = new Date();
  const objectName = [
    'blog',
    `${now.getUTCFullYear()}`,
    String(now.getUTCMonth() + 1).padStart(2, '0'),
    `${randomUUID()}.${EXTENSIONS[contentType]}`,
  ].join('/');

  const buffer = Buffer.from(await file.arrayBuffer());

  await getStorage()
    .bucket(BUCKET)
    .file(objectName)
    .save(buffer, {
      contentType,
      // Objects are immutable (random names), so they can cache forever.
      metadata: { cacheControl: 'public, max-age=31536000, immutable' },
    });

  const base = BASE_URL?.replace(/\/$/, '') ?? `https://storage.googleapis.com/${BUCKET}`;
  return `${base}/${objectName}`;
}

// Part product images (case picker cards, and eventually other parts). Their
// own bucket when configured, falling back to the blog bucket under a distinct
// prefix so nothing breaks before PARTS_MEDIA_BUCKET exists.
const PARTS_BUCKET = process.env.PARTS_MEDIA_BUCKET || process.env.BLOG_MEDIA_BUCKET;
const PARTS_BASE_URL = process.env.PARTS_MEDIA_BASE_URL;

/**
 * Upload a part product image to GCS and return its public URL.
 *
 * Same shape as uploadBlogImage (randomised object names, immutable caching)
 * for the same reasons. Kept separate rather than parameterised because the
 * two flows are configured independently and should be free to diverge.
 */
export async function uploadPartImage(file: File): Promise<string> {
  if (!PARTS_BUCKET) {
    throw new UploadError(
      'Neither PARTS_MEDIA_BUCKET nor BLOG_MEDIA_BUCKET is configured on this server'
    );
  }

  const contentType = file.type;
  if (!ALLOWED_IMAGE_TYPES.includes(contentType as (typeof ALLOWED_IMAGE_TYPES)[number])) {
    throw new UploadError(`Unsupported image type: ${contentType || 'unknown'}`);
  }
  if (file.size > MAX_IMAGE_BYTES) {
    throw new UploadError(
      `Image is ${(file.size / 1024 / 1024).toFixed(1)} MB; the limit is ${MAX_IMAGE_BYTES / 1024 / 1024} MB`
    );
  }

  const objectName = ['parts', `${randomUUID()}.${EXTENSIONS[contentType]}`].join('/');
  const buffer = Buffer.from(await file.arrayBuffer());

  await getStorage()
    .bucket(PARTS_BUCKET)
    .file(objectName)
    .save(buffer, {
      contentType,
      metadata: { cacheControl: 'public, max-age=31536000, immutable' },
    });

  const base =
    PARTS_BASE_URL?.replace(/\/$/, '') ?? `https://storage.googleapis.com/${PARTS_BUCKET}`;
  return `${base}/${objectName}`;
}
