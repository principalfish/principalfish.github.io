function previewMap(){
  $("#ticker").append("<div id=\"previewmap\"></div>");
  $('#previewbutton').attr("disabled", true);
  doSeats(0);
}


function doSeats(previewnumber){
  $("#previewmap").html("");

  if (previewNumber == relevantSeats.length){
    $("#previewmap").remove();
    reset();
    $('#previewbutton').attr("disabled", false);
    previewNumber = 0;

  }

  else {
    var seat = relevantSeats[previewNumber];


    $("#previewmap").append("<p id=\"boldpreview\">" + seat.name + "</p>")
    $("#previewmap").append("<p>" + seat.description + "</p>")
    $.each(seat.mapnumbers, function(i) {

      console.log(seat.mapnumbers[i]);
    })
    $("#previewmap").append("<p id=\"nextpreview\"><a href=\"#\" onclick=\"doSeats(previewNumber + 1)\">Next</a></p>")

    if (seat.number != -1) {
      zoomToClickedFilteredSeat(searchSeatData[seat.number - 1]);
    }

    previewNumber += 1;
  }

}


var previewNumber = 0;

relevantSeats = [
  {
  "name" : "A quick run through what will happen on the night. Click next to continue",
  "number" : -1,
  "mapnumbers" : [-1],
  "description" : ""
  },

  ////////

  {"name" : "11pm - Midnight: The first two seats expected to declare",
  "number" : 290,
  "mapnumbers" : [290, 603],
  "description" : "Both are safe Labour seats in the North East. <p>Sunderland South, part of which became Houghton and Sunderland South in 2010, was the first seat to declare on election night in all elections from 1992-2005.</p> "
  },

  ////////

  // {"name" : "Nuneaton", "number" : 430, "description" : "fdasdfsa"  },
  //
  //
  // {"name" : "Berwick-upon-Tweed", "number" : 40 }
]
