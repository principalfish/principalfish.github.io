function previewMap(){
  $("#ticker").append("<div id=\"previewmap\"></div>");
  doSeats(0);
}


function doSeats(previewnumber){
  $("#previewmap").html("");

  var seat = relevantSeats[previewNumber];
  console.log(seat);

  $("#previewmap").append("<p>" + seat.name + "</p>")
  $("#previewmap").append("<p><a href=\"#\" onclick=\"doSeats(previewNumber + 1)\">Next</a></p>")
  zoomToClickedFilteredSeat(searchSeatData[seat.number]);

  previewNumber += 1;
}


var previewNumber = 0;

relevantSeats = [
  {"name" : "Nuneaton", "number" : 429 },
  {"name" : "Berwick-upon-Tweed", "number" : 39 }
]
