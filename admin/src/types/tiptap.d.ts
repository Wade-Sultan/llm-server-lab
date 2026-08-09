import type { MarkdownStorage } from 'tiptap-markdown';

// tiptap-markdown registers its storage at runtime but ships no module
// augmentation, so `editor.storage.markdown` is untyped without this.
declare module '@tiptap/core' {
  interface Storage {
    markdown: MarkdownStorage;
  }
}
