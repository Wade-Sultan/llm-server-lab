import { NextResponse } from 'next/server';
import { MAX_IMAGE_BYTES, UploadError, uploadPartImage } from '@/lib/storage';

// Same shape as the blog upload route: the part form needs a URL back for a
// file picked mid-edit, before the part itself is saved.
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
    const url = await uploadPartImage(file);
    return NextResponse.json({ url });
  } catch (err) {
    if (err instanceof UploadError) {
      return NextResponse.json({ error: err.message }, { status: 400 });
    }
    console.error('Part image upload failed', err);
    return NextResponse.json({ error: 'Upload failed' }, { status: 500 });
  }
}
