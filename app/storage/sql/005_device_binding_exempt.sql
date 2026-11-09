ALTER TABLE tags ADD COLUMN device_binding_exempt INTEGER NOT NULL DEFAULT 0 CHECK (device_binding_exempt IN (0,1));
