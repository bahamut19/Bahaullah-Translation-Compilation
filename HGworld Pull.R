library(rvest)
library(tidyverse)

## Edit lines 6-8

book <- "Madinatut-Tawhid"
pages <- 1
column <- "GPT4_"

urltable <- tibble(NPage = 1:pages, URL = paste0("https://www.hgworld.org/ctw/index.php?title=", book,"/Page", NPage))
newbook <- tibble()

urlpage <- 1:pages
for (i in urlpage) {
  url <- as.character(urltable[i,2])
  webpage <- read_html(url)
  htmltable <- html_table(webpage)
  booktable <- tibble(Paragraph = (i*20-19):(i*20), Text = htmltable[[1]][[column]])
  newbook <- bind_rows(newbook, booktable)
}

completebook <- paste0(newbook$Paragraph, " ", newbook$Text)
finished <- gsub("\\s*\\([^\\)]+\\)", '', completebook)

write_lines(finished, file = paste0(book, ".txt"))

