import { NextResponse } from 'next/server';
import { MAX_IMAGE_BYTES, UploadError, uploadBlogImage } from '@/lib/storage';

// A route handler rather than a server action because the editor's image
// button and the cover-image picker both need a URL back for an arbitrary file
// picked mid-edit, before the post itself is saved.
export const runtime = 'nodejs';
export const maxDuration = 60;

export async function POST(request: Request) {
  let formData: FormData;
  try {
    formData = await request.formData();
  } catch {
    return NextResponse.json({ error: 'Expected multipart/form-data' }, { status: 400 });
  }

  const file = formData.get('file');
  if (!(file instanceof File) || file.size === 0) {
    return NextResponse.json({ error: 'No file provided' }, { status: 400 });
  }
  if (file.size > MAX_IMAGE_BYTES) {
    return NextResponse.json(
      { error: `Image exceeds the ${MAX_IMAGE_BYTES / 1024 / 1024} MB limit` },
      { status: 413 }
    );
  }

  try {
    const url = await uploadBlogImage(file);
    return NextResponse.json({ url });
  } catch (err) {
    if (err instanceof UploadError) {
      return NextResponse.json({ error: err.message }, { status: 400 });
    }
    console.error('Blog image upload failed', err);
    return NextResponse.json({ error: 'Upload failed' }, { status: 500 });
  }
}
