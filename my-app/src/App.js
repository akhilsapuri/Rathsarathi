import React, { useState, useEffect } from 'react';

function App() {
  const [data, setData] = useState('');

  useEffect(() => {
    fetch('http://localhost:8000')
      .then((res) => res.json())
      .then((data) => setData(data.message))
      .catch((err) => console.error("Error fetching data:", err));
  }, []);

  return (
    <div>
      <h1>Backend Message:</h1>
      <p>{data ? data : "Loading..."}</p>
    </div>
  );
}

export default App;
