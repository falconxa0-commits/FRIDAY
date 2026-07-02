import React from 'react';

const CATEGORIES = {
    "Social": ["Twitter", "LinkedIn", "Discord", "Slack", "Reddit", "Facebook", "Instagram", "Mastodon", "Telegram", "WhatsApp"],
    "Dev": ["GitHub", "GitLab", "Bitbucket", "Jira", "Trello", "Heroku", "Netlify", "Vercel", "DigitalOcean", "Linode"],
    "Finance": ["Stripe", "PayPal", "Coinbase", "Binance", "Plaid", "Robinhood", "Wise", "Revolut", "Venmo", "CashApp"],
    "Productivity": ["Notion", "Evernote", "Todoist", "GoogleDrive", "Dropbox", "Slack", "MicrosoftTeams", "Zoom", "Calendly", "Asana"]
};

export default function Integrations() {
  return (
    <div style={{background: '#050810', color: 'white', minHeight: '100vh', padding: '20px', fontFamily: 'sans-serif'}}>
      <h1 style={{color: '#7C3AED'}}>Universal Integrations (100+)</h1>
      <div style={{display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '20px'}}>
        {Object.entries(CATEGORIES).map(([cat, services]) => (
          <div key={cat} style={{background: '#0d1120', padding: '15px', borderRadius: '10px', border: '1px solid #1a2040'}}>
            <h3 style={{color: '#06B6D4', borderBottom: '1px solid #7C3AED'}}>{cat}</h3>
            {services.map(s => (
              <div key={s} style={{fontSize: '12px', margin: '5px 0'}}>
                <span style={{color: '#10B981'}}>●</span> {s} (Ready)
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
