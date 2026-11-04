<?php

class BugReportEntity extends ModelEntity
{
    protected static function getSchemaDefinition(): Schema
    {
        return Schema::table(
            'bug_reports',
            IntColumn::create('id')->primary(),
            VarcharColumn::create('title', 100),
            TextColumn::create('steps_to_recreate')
        );
    }

    public function getTitle(): ?string
    {
        return $this->getField('title');
    }
}
