import React, { useState } from 'react';

export default function Home() {
  const [msg, setMsg] = useState("");
  const [resp, setResp] = useState("");

  const askFriday = async () => {
    const r = await fetch("http://localhost:8000/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: msg })
    });
    const d = await r.json();
    setResp(d.response);
  };

  return (
    <div style={{background: '#050810', color: 'white', minHeight: '100vh', padding: '20px', fontFamily: 'sans-serif'}}>
      <h1 style={{color: '#7C3AED'}}>Friday Web Dashboard</h1>
      <div style={{border: '1px solid #1a2040', padding: '20px', borderRadius: '10px'}}>
        <input 
          value={msg} 
          onChange={(e) => setMsg(e.target.value)} 
          placeholder="Type a message..." 
          style={{width: '80%', padding: '10px', background: '#0d1120', color: 'white', border: '1px solid #7C3AED'}}
        />
        <button onClick={askFriday} style={{padding: '10px 20px', background: '#7C3AED', color: 'white', border: 'none', marginLeft: '10px'}}>Send</button>
      </div>
      <div style={{marginTop: '20px', color: '#06B6D4'}}>
        <strong>Friday:</strong> {resp}
      </div>
    </div>
  );
}
