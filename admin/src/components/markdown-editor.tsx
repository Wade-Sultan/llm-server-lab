'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { EditorContent, useEditor, type Editor } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Image from '@tiptap/extension-image';
import { Markdown } from 'tiptap-markdown';
import {
  Bold, Code, Heading2, Heading3, ImageIcon, Italic, Link2, List, ListOrdered,
  Minus, Quote, Redo2, Strikethrough, Undo2,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { uploadImage } from '@/lib/upload-client';

// WYSIWYG editing, Markdown storage. tiptap-markdown handles both directions,
// so what lands in the DB is the same Markdown the public site renders with
// react-markdown — no HTML sanitising needed on the read path.

type ToolButton = {
  icon: React.ElementType;
  title: string;
  run: (editor: Editor) => void;
  isActive?: (editor: Editor) => boolean;
};

const TOOLS: (ToolButton | 'divider')[] = [
  { icon: Bold, title: 'Bold', run: (e) => e.chain().focus().toggleBold().run(), isActive: (e) => e.isActive('bold') },
  { icon: Italic, title: 'Italic', run: (e) => e.chain().focus().toggleItalic().run(), isActive: (e) => e.isActive('italic') },
  { icon: Strikethrough, title: 'Strikethrough', run: (e) => e.chain().focus().toggleStrike().run(), isActive: (e) => e.isActive('strike') },
  { icon: Code, title: 'Inline code', run: (e) => e.chain().focus().toggleCode().run(), isActive: (e) => e.isActive('code') },
  'divider',
  { icon: Heading2, title: 'Heading 2', run: (e) => e.chain().focus().toggleHeading({ level: 2 }).run(), isActive: (e) => e.isActive('heading', { level: 2 }) },
  { icon: Heading3, title: 'Heading 3', run: (e) => e.chain().focus().toggleHeading({ level: 3 }).run(), isActive: (e) => e.isActive('heading', { level: 3 }) },
  { icon: List, title: 'Bullet list', run: (e) => e.chain().focus().toggleBulletList().run(), isActive: (e) => e.isActive('bulletList') },
  { icon: ListOrdered, title: 'Numbered list', run: (e) => e.chain().focus().toggleOrderedList().run(), isActive: (e) => e.isActive('orderedList') },
  { icon: Quote, title: 'Blockquote', run: (e) => e.chain().focus().toggleBlockquote().run(), isActive: (e) => e.isActive('blockquote') },
  { icon: Minus, title: 'Divider', run: (e) => e.chain().focus().setHorizontalRule().run() },
  'divider',
];

function ToolbarButton({
  icon: Icon, title, active, onClick,
}: {
  icon: React.ElementType;
  title: string;
  active?: boolean;
  onClick: () => void;
}) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      title={title}
      aria-label={title}
      aria-pressed={active ?? false}
      className={cn('h-8 w-8 p-0', active && 'bg-accent text-accent-foreground')}
      onClick={onClick}
    >
      <Icon className="h-4 w-4" />
    </Button>
  );
}

export function MarkdownEditor({
  value,
  onChange,
}: {
  value: string;
  onChange: (markdown: string) => void;
}) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const editor = useEditor({
    // The editor renders identically on server and client, but Tiptap warns
    // unless SSR rendering is opted out of explicitly.
    immediatelyRender: false,
    extensions: [
      // Link ships inside StarterKit v3, so it is configured here rather than
      // registered separately — a second registration is a duplicate-extension
      // warning and unpredictable ordering. Image does not ship in StarterKit.
      StarterKit.configure({ link: { openOnClick: false, autolink: true } }),
      Image.configure({ HTMLAttributes: { class: 'rounded-md max-w-full' } }),
      Markdown.configure({ html: false, transformPastedText: true, linkify: true }),
    ],
    content: value,
    editorProps: {
      attributes: {
        class: 'prose-editor focus:outline-none min-h-[320px] px-4 py-3',
      },
    },
    onUpdate: ({ editor }) => {
      onChange(editor.storage.markdown.getMarkdown());
    },
  });

  // Repopulate when the form swaps to a different post (the dialog reuses the
  // component). Guarded against echoing back the user's own keystrokes.
  useEffect(() => {
    if (!editor) return;
    if (value !== editor.storage.markdown.getMarkdown()) {
      editor.commands.setContent(value, { emitUpdate: false });
    }
    // Only re-sync on an externally-driven value change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editor, value]);

  const insertImage = useCallback(
    async (file: File) => {
      setError(null);
      setUploading(true);
      try {
        const url = await uploadImage(file);
        editor?.chain().focus().setImage({ src: url, alt: file.name }).run();
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Upload failed');
      } finally {
        setUploading(false);
      }
    },
    [editor]
  );

  const setLink = useCallback(() => {
    if (!editor) return;
    const previous = editor.getAttributes('link').href as string | undefined;
    const url = window.prompt('Link URL', previous ?? 'https://');
    if (url === null) return;
    if (url === '') {
      editor.chain().focus().extendMarkRange('link').unsetLink().run();
      return;
    }
    editor.chain().focus().extendMarkRange('link').setLink({ href: url }).run();
  }, [editor]);

  if (!editor) return null;

  return (
    <div className="rounded-md border bg-background">
      <div className="flex flex-wrap items-center gap-0.5 border-b px-2 py-1.5">
        {TOOLS.map((tool, i) =>
          tool === 'divider' ? (
            <div key={`d${i}`} className="mx-1 h-5 w-px bg-border" />
          ) : (
            <ToolbarButton
              key={tool.title}
              icon={tool.icon}
              title={tool.title}
              active={tool.isActive?.(editor)}
              onClick={() => tool.run(editor)}
            />
          )
        )}
        <ToolbarButton icon={Link2} title="Link" active={editor.isActive('link')} onClick={setLink} />
        <ToolbarButton
          icon={ImageIcon}
          title={uploading ? 'Uploading…' : 'Insert image'}
          onClick={() => fileInput.current?.click()}
        />
        <div className="ml-auto flex items-center gap-0.5">
          {uploading && <span className="mr-2 text-xs text-muted-foreground">Uploading…</span>}
          <ToolbarButton icon={Undo2} title="Undo" onClick={() => editor.chain().focus().undo().run()} />
          <ToolbarButton icon={Redo2} title="Redo" onClick={() => editor.chain().focus().redo().run()} />
        </div>
      </div>

      <input
        ref={fileInput}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          // Reset so picking the same file twice still fires a change event.
          e.target.value = '';
          if (file) void insertImage(file);
        }}
      />

      <EditorContent
        editor={editor}
        onDrop={(e) => {
          const file = Array.from(e.dataTransfer.files).find((f) => f.type.startsWith('image/'));
          if (!file) return;
          e.preventDefault();
          void insertImage(file);
        }}
        onPaste={(e) => {
          const file = Array.from(e.clipboardData.files).find((f) => f.type.startsWith('image/'));
          if (!file) return;
          e.preventDefault();
          void insertImage(file);
        }}
      />

      {error && <p className="border-t px-4 py-2 text-sm text-destructive">{error}</p>}
    </div>
  );
}
