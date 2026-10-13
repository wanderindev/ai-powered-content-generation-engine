import { useEditor, EditorContent, NodeViewWrapper } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Link from '@tiptap/extension-link';
import Image from '@tiptap/extension-image';
import { forwardRef, useEffect, useImperativeHandle } from 'react';

const SIZE_CLASSES = {
  small: 'max-w-[300px]',
  medium: 'max-w-[500px]',
  large: 'max-w-full',
};

const CustomImage = Image.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      size: {
        default: 'medium',
        parseHTML: (el) => el.getAttribute('data-size') || 'medium',
        renderHTML: (attrs) => ({ 'data-size': attrs.size }),
      },
    };
  },

  addNodeView() {
    return ({ node, editor, getPos }) => {
      const container = document.createElement('div');
      container.classList.add('image-node-view');

      const img = document.createElement('img');
      img.src = node.attrs.src;
      if (node.attrs.alt) img.alt = node.attrs.alt;
      if (node.attrs.title) img.title = node.attrs.title;
      img.setAttribute('data-size', node.attrs.size || 'medium');
      img.classList.add('rounded-lg', 'my-2', 'h-auto', 'cursor-pointer');
      applySizeClass(img, node.attrs.size || 'medium');

      const bar = document.createElement('div');
      bar.classList.add('image-size-bar');
      bar.style.display = 'none';

      for (const size of ['small', 'medium', 'large']) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.textContent = size[0].toUpperCase();
        btn.title = size.charAt(0).toUpperCase() + size.slice(1);
        btn.classList.add('image-size-btn');
        if (node.attrs.size === size) btn.classList.add('active');
        btn.addEventListener('mousedown', (e) => {
          e.preventDefault();
          e.stopPropagation();
          const pos = getPos();
          if (typeof pos === 'number') {
            editor.chain().focus().setNodeSelection(pos).updateAttributes('image', { size }).run();
          }
        });
        bar.appendChild(btn);
      }

      container.appendChild(bar);
      container.appendChild(img);

      img.addEventListener('click', () => {
        const pos = getPos();
        if (typeof pos === 'number') {
          editor.chain().focus().setNodeSelection(pos).run();
        }
      });

      return {
        dom: container,
        update(updatedNode) {
          if (updatedNode.type.name !== 'image') return false;
          img.src = updatedNode.attrs.src;
          if (updatedNode.attrs.alt) img.alt = updatedNode.attrs.alt;
          img.setAttribute('data-size', updatedNode.attrs.size || 'medium');
          applySizeClass(img, updatedNode.attrs.size || 'medium');
          bar.querySelectorAll('.image-size-btn').forEach((b, i) => {
            const sizes = ['small', 'medium', 'large'];
            b.classList.toggle('active', updatedNode.attrs.size === sizes[i]);
          });
          return true;
        },
        selectNode() {
          container.classList.add('selected');
          bar.style.display = '';
        },
        deselectNode() {
          container.classList.remove('selected');
          bar.style.display = 'none';
        },
        destroy() {},
      };
    };
  },
});

function applySizeClass(img, size) {
  for (const cls of Object.values(SIZE_CLASSES)) {
    img.classList.remove(cls);
  }
  img.classList.add(SIZE_CLASSES[size] || SIZE_CLASSES.medium);
}

function ToolbarButton({ onClick, active, children, title }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className={`px-2 py-1 text-sm rounded font-medium ${
        active
          ? 'bg-indigo-100 text-indigo-700'
          : 'text-gray-600 hover:bg-gray-100'
      }`}
    >
      {children}
    </button>
  );
}

function ToolbarDivider() {
  return <div className="w-px h-6 bg-gray-300 mx-1" />;
}

function Toolbar({ editor, onImageButtonClick }) {
  if (!editor) return null;

  const addLink = () => {
    const url = window.prompt('URL:');
    if (url) {
      editor.chain().focus().extendMarkRange('link').setLink({ href: url }).run();
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-0.5 border-b border-gray-200 px-2 py-1.5 bg-gray-50">
      {[1, 2, 3, 4].map((level) => (
        <ToolbarButton
          key={level}
          onClick={() => editor.chain().focus().toggleHeading({ level }).run()}
          active={editor.isActive('heading', { level })}
          title={`Heading ${level}`}
        >
          H{level}
        </ToolbarButton>
      ))}

      <ToolbarDivider />

      <ToolbarButton
        onClick={() => editor.chain().focus().toggleBold().run()}
        active={editor.isActive('bold')}
        title="Bold"
      >
        <strong>B</strong>
      </ToolbarButton>
      <ToolbarButton
        onClick={() => editor.chain().focus().toggleItalic().run()}
        active={editor.isActive('italic')}
        title="Italic"
      >
        <em>I</em>
      </ToolbarButton>

      <ToolbarDivider />

      <ToolbarButton
        onClick={() => editor.chain().focus().toggleBulletList().run()}
        active={editor.isActive('bulletList')}
        title="Bullet list"
      >
        &bull; List
      </ToolbarButton>
      <ToolbarButton
        onClick={() => editor.chain().focus().toggleOrderedList().run()}
        active={editor.isActive('orderedList')}
        title="Numbered list"
      >
        1. List
      </ToolbarButton>

      <ToolbarDivider />

      <ToolbarButton
        onClick={() => editor.chain().focus().toggleBlockquote().run()}
        active={editor.isActive('blockquote')}
        title="Blockquote"
      >
        &ldquo; Quote
      </ToolbarButton>
      <ToolbarButton
        onClick={() => editor.chain().focus().setHorizontalRule().run()}
        title="Horizontal rule"
      >
        &mdash; HR
      </ToolbarButton>
      <ToolbarButton
        onClick={() => editor.chain().focus().toggleCodeBlock().run()}
        active={editor.isActive('codeBlock')}
        title="Code block"
      >
        {'<>'} Code
      </ToolbarButton>

      <ToolbarDivider />

      <ToolbarButton
        onClick={addLink}
        active={editor.isActive('link')}
        title="Insert link"
      >
        Link
      </ToolbarButton>
      {editor.isActive('link') && (
        <ToolbarButton
          onClick={() => editor.chain().focus().unsetLink().run()}
          title="Remove link"
        >
          Unlink
        </ToolbarButton>
      )}

      {onImageButtonClick && (
        <>
          <ToolbarDivider />
          <ToolbarButton
            onClick={onImageButtonClick}
            active={editor.isActive('image')}
            title="Insert image"
          >
            Img
          </ToolbarButton>
        </>
      )}
    </div>
  );
}

const RichTextEditor = forwardRef(function RichTextEditor({ content, onChange, onImageButtonClick }, ref) {
  const editor = useEditor({
    extensions: [
      StarterKit,
      Link.configure({ openOnClick: false }),
      CustomImage.configure({ inline: false, allowBase64: false }),
    ],
    content: content || '',
    onUpdate: ({ editor }) => {
      onChange?.(editor.getHTML());
    },
  });

  useImperativeHandle(ref, () => ({
    insertImage(src, alt) {
      if (editor) {
        editor.chain().focus().setImage({ src, alt, size: 'medium' }).run();
      }
    },
  }), [editor]);

  useEffect(() => {
    if (editor && content !== undefined && content !== editor.getHTML()) {
      editor.commands.setContent(content || '', false);
    }
  }, [content, editor]);

  return (
    <div className="border border-gray-300 rounded-lg overflow-hidden h-full flex flex-col">
      <Toolbar editor={editor} onImageButtonClick={onImageButtonClick} />
      <div className="article-content p-4 flex-1 min-h-0 overflow-y-auto [&_.tiptap:focus]:outline-none">
        <EditorContent editor={editor} />
      </div>
    </div>
  );
});

export default RichTextEditor;
