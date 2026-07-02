import React, { useState, useEffect } from 'react';

export default function Memory() {
  const [memories, setMemories] = useState([]);
  const [loading, setLoading] = useState(true);

  const fetchMemories = async () => {
    try {
      const r = await fetch("http://localhost:8000/api/memory/all", {
        headers: { "Authorization": "Bearer dev_token" }
      });
      const d = await r.json();
      setMemories(d);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const deleteMemory = async (id) => {
    await fetch(\`http://localhost:8000/api/memory/\${id}\`, {
      method: "DELETE",
      headers: { "Authorization": "Bearer dev_token" }
    });
    fetchMemories();
  };

  useEffect(() => { fetchMemories(); }, []);

  return (
    <div style={{background: '#050810', color: 'white', minHeight: '100vh', padding: '20px', fontFamily: 'sans-serif'}}>
      <h1 style={{color: '#7C3AED'}}>Memory Inspector</h1>
      {loading ? <p>Loading...</p> : (
        <div style={{display: 'flex', flexDirection: 'column', gap: '10px'}}>
          {memories.map((m, i) => (
            <div key={i} style={{background: '#0d1120', padding: '15px', borderRadius: '10px', border: '1px solid #1a2040', display: 'flex', justifyContent: 'space-between'}}>
              <div>
                <p><strong>Content:</strong> {m.content}</p>
                <p style={{fontSize: '12px', color: '#64748B'}}>Category: {m.metadata?.category || "None"}</p>
              </div>
              <button onClick={() => deleteMemory(i)} style={{background: '#EF4444', color: 'white', border: 'none', padding: '5px 10px', borderRadius: '5px', cursor: 'pointer'}}>Delete</button>
            </div>
          ))}
          {memories.length === 0 && <p>No memories found.</p>}
        </div>
      )}
    </div>
  );
}
